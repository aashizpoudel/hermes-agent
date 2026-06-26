"""Slash command routes."""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from hermes_cli.config import load_config

from ..runner import get_runner
from ..state import _check_bearer
from .messages import CommandBody

router = APIRouter()


_HELP_TEXT = (
    "Available commands:\n"
    "  /help   Show this message.\n"
    "  /new    Start a new session.\n"
    "  /clear  Clear the conversation history (current session only).\n"
    "  /model  Show the active model and provider.\n"
    "  /quit   Shut the chat server down.\n"
)


@router.post("/api/command")
async def command(body: CommandBody, request: Request):
    _check_bearer(request)
    runner = get_runner()
    name = (body.name or "").lstrip("/").strip().lower()

    if name == "help":
        return {"text": _HELP_TEXT}

    if name == "new":
        if runner.active_stream_id:
            runner.interrupt_active_turn("(cancelled: starting a new session)")
            if not runner.wait_for_idle():
                raise HTTPException(status_code=409, detail="could not stop the active turn")
        new_id = runner.start_new_session()
        return {"text": f"Started new session ({new_id}).", "session_id": new_id}

    if name == "clear":
        runner.conversation_history = []
        runner.agent = None  # force rebuild so stale state is dropped
        db = runner._ensure_session_db()
        if db is not None:
            try:
                db.clear_messages(runner.session_id)
            except Exception:  # noqa: BLE001
                pass
        return {"text": "Conversation cleared."}

    if name == "model":
        try:
            cfg = load_config()
            mc = cfg.get("model") or {}
            model = mc.get("default") or "(unset)"
            provider = mc.get("provider") or "auto"
            return {"text": f"Model: {model}\nProvider: {provider}"}
        except Exception as e:  # noqa: BLE001
            return {"text": f"Failed to read model config: {e}"}

    if name == "quit":
        def _bye() -> None:
            time.sleep(0.2)
            os._exit(0)

        threading.Thread(target=_bye, daemon=True).start()
        return {"text": "Closing...", "shutdown": True}

    return JSONResponse(
        status_code=404,
        content={"error": f"command not implemented yet: /{name}"},
    )
