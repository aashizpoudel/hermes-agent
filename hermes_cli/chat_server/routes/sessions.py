"""Session management routes."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..runner import get_runner
from ..state import _check_bearer

router = APIRouter()

logger = logging.getLogger(__name__)


class _SessionIdBody(BaseModel):
    id: str


class _SessionRenameBody(BaseModel):
    id: str
    title: str


class _ModelSwitchBody(BaseModel):
    provider: str
    model: str


def _session_meta_dict(row: Dict[str, Any], current_id: str, message_count: int) -> Dict[str, Any]:
    """Project a SessionDB row to the trimmed shape the frontend expects."""
    sid = str(row.get("id") or "")
    started_at = row.get("started_at") or 0
    last_active = row.get("last_active") or row.get("ended_at") or started_at or 0

    def _iso(ts: Any) -> str:
        try:
            from datetime import datetime, timezone
            return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
        except Exception:
            return ""

    title = row.get("title") or row.get("preview") or ""
    if not title:
        title = sid[:12] or "(untitled)"
    return {
        "id": sid,
        "title": str(title),
        "created_at": _iso(started_at),
        "updated_at": _iso(last_active),
        "is_current": sid == current_id,
        "message_count": int(row.get("message_count") or message_count or 0),
    }


@router.get("/api/sessions")
async def list_sessions(request: Request) -> Dict[str, Any]:
    """List persisted chat sessions plus the in-memory current one."""
    _check_bearer(request)
    runner = get_runner()
    db = runner._ensure_session_db()
    items: List[Dict[str, Any]] = []
    seen: set = set()
    if db is not None:
        try:
            rows = db.list_sessions_rich(
                source="chat-pwa",
                limit=200,
                include_children=False,
            ) or []
        except Exception as e:  # noqa: BLE001
            logger.warning("list_sessions_rich failed: %s", e)
            rows = []
        for row in rows:
            meta = _session_meta_dict(
                row, runner.session_id, int(row.get("message_count") or 0)
            )
            items.append(meta)
            seen.add(meta["id"])

    # Always include the in-memory current session (even if not yet persisted
    # or if SessionDB is unavailable).
    if runner.session_id not in seen:
        items.append({
            "id": runner.session_id,
            "title": "(current)",
            "created_at": "",
            "updated_at": "",
            "is_current": True,
            "message_count": len(runner.conversation_history),
        })

    items.sort(key=lambda m: m.get("updated_at") or "", reverse=True)
    return {"items": items}


@router.post("/api/sessions/new")
async def new_session(request: Request) -> Dict[str, Any]:
    """Create a fresh session, switch to it, reset conversation history."""
    _check_bearer(request)
    runner = get_runner()
    if runner.active_stream_id:
        runner.interrupt_active_turn("(cancelled: switching to a new session)")
        if not runner.wait_for_idle():
            raise HTTPException(status_code=409, detail="could not stop the active turn")
    new_id = runner.start_new_session()
    return {"id": new_id, "is_current": True}


@router.post("/api/sessions/switch")
async def switch_session(body: _SessionIdBody, request: Request) -> Dict[str, Any]:
    """Load a previously saved session into the runner and return its history."""
    _check_bearer(request)
    runner = get_runner()
    if runner.active_stream_id:
        runner.interrupt_active_turn("(cancelled: switching sessions)")
        if not runner.wait_for_idle():
            raise HTTPException(status_code=409, detail="could not stop the active turn")
    sid = (body.id or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="missing session id")
    try:
        meta = runner.bind_session(sid)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown session: {sid}")
    payload = _session_meta_dict(
        meta, runner.session_id, len(runner.conversation_history)
    )
    payload["messages"] = _replay_messages(runner.conversation_history)
    return payload


def _replay_messages(history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Flatten conversation_history into the {role, text} shape the UI replays.

    System messages and tool-call plumbing are dropped — replay only shows the
    visible user/assistant turns. Multi-part user content (image attachments)
    is collapsed to its text part; image URLs survive as ``/files/<token>``
    references already inlined by the live MEDIA: rewriter on the original
    response.
    """
    out: List[Dict[str, Any]] = []
    for msg in history or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role not in ("user", "assistant"):
            continue
        content = msg.get("content")
        if isinstance(content, list):
            text_parts = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text" and part.get("text"):
                    text_parts.append(part["text"])
            text = "\n".join(text_parts).strip()
        else:
            text = (content or "").strip() if isinstance(content, str) else ""
        if not text:
            continue
        out.append({"role": role, "text": text})
    return out


@router.post("/api/sessions/rename")
async def rename_session(body: _SessionRenameBody, request: Request) -> Dict[str, Any]:
    """Persist a new title for a session via SessionDB.set_session_title."""
    _check_bearer(request)
    runner = get_runner()
    sid = (body.id or "").strip()
    title = (body.title or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="missing session id")
    db = runner._ensure_session_db()
    if db is None:
        raise HTTPException(status_code=503, detail="session storage unavailable")
    try:
        ok = db.set_session_title(sid, title)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.warning("set_session_title failed for %s: %s", sid, e)
        raise HTTPException(status_code=500, detail="rename failed")
    if not ok:
        raise HTTPException(status_code=404, detail=f"unknown session: {sid}")
    try:
        row = db.get_session(sid) or {"id": sid, "title": title}
    except Exception:
        row = {"id": sid, "title": title}
    return _session_meta_dict(row, runner.session_id, 0)


@router.post("/api/sessions/delete")
async def delete_session(body: _SessionIdBody, request: Request) -> Dict[str, Any]:
    """Delete a session; if it was active, switch to another session if possible."""
    _check_bearer(request)
    runner = get_runner()
    sid = (body.id or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="missing session id")
    deleting_active = sid == runner.session_id
    if deleting_active and runner.active_stream_id:
        runner.interrupt_active_turn("(cancelled: deleting the active session)")
        if not runner.wait_for_idle():
            raise HTTPException(status_code=409, detail="could not stop the active turn")
    db = runner._ensure_session_db()
    if db is None:
        raise HTTPException(status_code=503, detail="session storage unavailable")
    try:
        deleted = db.delete_session(sid)
    except Exception as e:  # noqa: BLE001
        logger.warning("delete_session failed for %s: %s", sid, e)
        raise HTTPException(status_code=500, detail="delete failed")

    was_active = bool(deleted) and sid == runner.session_id
    current_meta: Optional[Dict[str, Any]] = None
    created_new = False
    if was_active:
        # Prefer another existing session. Only create a new one when the
        # deleted session was the last remaining chat-pwa session.
        current_meta, created_new = runner.bind_latest_session_or_create()
    payload = {
        "deleted": bool(deleted),
        "current_id": runner.session_id,
        "switched": bool(was_active),
    }
    if was_active:
        payload["created_new"] = created_new
        payload["current"] = _session_meta_dict(
            current_meta or {"id": runner.session_id},
            runner.session_id,
            len(runner.conversation_history),
        )
        payload["messages"] = _replay_messages(runner.conversation_history)
    return payload
