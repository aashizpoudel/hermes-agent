"""Message and streaming routes."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..runner import get_runner
from ..state import (
    _CANCELS,
    _STREAMS,
    _check_bearer,
    _check_token_query_or_header,
    _resolve_file,
)

router = APIRouter()

logger = logging.getLogger(__name__)


class MessageBody(BaseModel):
    text: str
    attachments: Optional[List[str]] = None  # file tokens from /api/upload


class CommandBody(BaseModel):
    name: str
    args: Optional[str] = None


class _ClarifyResponseBody(BaseModel):
    id: str
    answer: str


@router.get("/api/health")
async def health() -> Dict[str, Any]:
    runner = get_runner()
    return {"ok": True, "session_id": runner.session_id}


@router.post("/api/message")
async def post_message(body: MessageBody, request: Request) -> Dict[str, Any]:
    _check_bearer(request)
    runner = get_runner()

    user_text = (body.text or "").strip()
    attachments = body.attachments or []

    # Prepend [Image: <abs_path>] tokens for each attachment so the agent
    # sees the file path in the message it processes.
    prefix_parts: List[str] = []
    for tok in attachments:
        path = _resolve_file(tok)
        if path is None:
            raise HTTPException(status_code=400, detail=f"unknown attachment: {tok}")
        prefix_parts.append(f"[Image: {path}]")

    full_text = "\n".join(prefix_parts + [user_text]) if prefix_parts else user_text
    if not full_text:
        raise HTTPException(status_code=400, detail="empty message")

    # Refuse if a turn is already in flight — single-session MVP.
    if runner.active_stream_id:
        raise HTTPException(status_code=409, detail="another turn is in progress")

    stream_id = uuid.uuid4().hex
    loop = asyncio.get_running_loop()
    runner.loop = loop
    _STREAMS[stream_id] = asyncio.Queue()
    _CANCELS[stream_id] = runner.cancel_flag

    def _worker() -> None:
        try:
            runner.run_turn(stream_id, full_text)
        except Exception:  # noqa: BLE001
            logger.exception("worker thread crashed")

    t = threading.Thread(target=_worker, name=f"hermes-chat-{stream_id[:8]}", daemon=True)
    runner.worker_thread = t
    t.start()
    return {"stream_id": stream_id}


async def _sse_iter(stream_id: str):
    """Yield raw SSE-formatted bytes for the given stream."""
    q = _STREAMS.get(stream_id)
    if q is None:
        yield b"event: error\ndata: {\"message\": \"unknown stream\"}\n\n"
        return
    try:
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=60.0)
            except asyncio.TimeoutError:
                # heartbeat comment so reverse proxies don't kill the conn
                yield b": ping\n\n"
                continue
            etype = event.get("type", "message")
            payload = {k: v for k, v in event.items() if k != "type"}
            data = json.dumps(payload, ensure_ascii=False, default=str)
            yield f"event: {etype}\ndata: {data}\n\n".encode("utf-8")
            if etype in ("done", "error"):
                return
    finally:
        _STREAMS.pop(stream_id, None)
        _CANCELS.pop(stream_id, None)


@router.get("/api/stream/{stream_id}")
async def stream(stream_id: str, request: Request, token: Optional[str] = Query(None)):
    _check_token_query_or_header(request, token)
    if stream_id not in _STREAMS:
        raise HTTPException(status_code=404, detail="unknown stream_id")

    # We emit fully-formed SSE frames ourselves; sse_starlette would
    # wrap them again, so we just use StreamingResponse directly.  The
    # _HAS_SSE_STARLETTE flag is kept above so a future caller can swap
    # in EventSourceResponse without changing the public API.
    return StreamingResponse(
        _sse_iter(stream_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/api/cancel/{stream_id}")
async def cancel(stream_id: str, request: Request) -> Dict[str, Any]:
    _check_bearer(request)
    runner = get_runner()
    flag = _CANCELS.get(stream_id)
    if flag is None:
        raise HTTPException(status_code=404, detail="unknown stream_id")
    flag.set()
    runner.abort_pending_clarify(
        "The clarify prompt was cancelled because the turn was stopped. "
        "Use your best judgement and continue."
    )
    # Signal the agent to interrupt its tool-calling loop so long-running
    # operations (terminal commands, API calls, etc.) are aborted promptly
    # instead of running to completion.
    if runner.agent is not None:
        try:
            runner.agent.interrupt()
        except Exception:
            logger.debug("agent.interrupt() failed", exc_info=True)
    # Push a synthetic done so the frontend closes the EventSource.
    q = _STREAMS.get(stream_id)
    if q is not None:
        try:
            q.put_nowait({"type": "done", "final_text": "(cancelled)"})
        except Exception:
            pass
    return {
        "ok": True,
        "note": "agent interrupted — in-flight tool calls are being aborted",
    }


@router.post("/api/clarify/respond")
async def clarify_respond(body: _ClarifyResponseBody, request: Request) -> Dict[str, Any]:
    _check_bearer(request)
    runner = get_runner()
    request_id = (body.id or "").strip()
    answer = (body.answer or "").strip()
    if not request_id:
        raise HTTPException(status_code=400, detail="missing clarify id")
    if not answer:
        raise HTTPException(status_code=400, detail="missing clarify answer")
    if not runner.resolve_clarify(request_id, answer):
        raise HTTPException(status_code=404, detail="unknown clarify request")
    return {"ok": True}
