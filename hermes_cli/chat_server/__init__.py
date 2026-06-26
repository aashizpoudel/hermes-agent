"""Hermes Chat — FastAPI backend for the PWA chat frontend.

This package provides a lightweight chat server that drives the Hermes
``AIAgent`` from a PWA frontend bundled under ``hermes_cli/chat_static/``.
"""

from __future__ import annotations

import logging
import secrets
import sys
import threading
import time
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.staticfiles import StaticFiles
except ImportError as e:  # pragma: no cover - surfaced at boot
    raise SystemExit(
        "Hermes Chat requires fastapi and uvicorn.\n"
        f"Install with: {sys.executable} -m pip install 'fastapi' 'uvicorn[standard]'\n"
        f"Import error: {e}"
    )

# Optional: sse_starlette for nicer SSE.  Fall back to a hand-rolled
# StreamingResponse if it isn't available — we don't want a hard dep.
try:  # pragma: no cover
    from sse_starlette.sse import EventSourceResponse  # type: ignore
    _HAS_SSE_STARLETTE = True
except Exception:  # pragma: no cover
    EventSourceResponse = None  # type: ignore
    _HAS_SSE_STARLETTE = False

from .runner import get_runner
from .state import CHAT_STATIC_DIR, _SESSION_TOKEN, _BOUND_HOST, _BOUND_PORT, _load_auth_sessions

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="Hermes Chat", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include route routers
from .routes.auth import router as auth_router
from .routes.messages import router as messages_router
from .routes.files import router as files_router
from .routes.sessions import router as sessions_router
from .routes.models import router as models_router
from .routes.commands import router as commands_router

app.include_router(auth_router)
app.include_router(messages_router)
app.include_router(files_router)
app.include_router(sessions_router)
app.include_router(models_router)
app.include_router(commands_router)

# Static assets at /static/...
if CHAT_STATIC_DIR.is_dir():
    app.mount(
        "/static",
        StaticFiles(directory=str(CHAT_STATIC_DIR)),
        name="chat-static",
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _load_or_init_password() -> str:
    """Return the chatui auth password from config, generating one on first run.

    Persisting the secret to ``chatui.password`` lets the URL stay the
    same across restarts (so cached service-worker shells keep working,
    and the user can pick a memorable value by hand-editing the file).
    """
    try:
        from hermes_cli.config import load_config, save_config
    except Exception as e:  # noqa: BLE001
        logger.warning("could not import config helpers, falling back to ephemeral token: %s", e)
        return secrets.token_urlsafe(16)

    try:
        cfg = load_config() or {}
    except Exception as e:  # noqa: BLE001
        logger.warning("could not load config, falling back to ephemeral token: %s", e)
        return secrets.token_urlsafe(16)

    chatui_cfg = cfg.get("chatui") if isinstance(cfg.get("chatui"), dict) else {}
    pw = (chatui_cfg.get("password") or "").strip() if isinstance(chatui_cfg.get("password"), str) else ""
    if pw:
        return pw

    pw = secrets.token_urlsafe(16)
    chatui_cfg["password"] = pw
    cfg["chatui"] = chatui_cfg
    try:
        save_config(cfg)
        logger.info("Generated initial chatui.password — change in ~/.hermes/config.yaml if you want a memorable one.")
    except Exception as e:  # noqa: BLE001
        logger.warning("could not persist chatui.password (will reuse this run only): %s", e)
    return pw


def start_server(
    host: str = "127.0.0.1",
    port: int = 9120,
    token: Optional[str] = None,
    open_browser: bool = True,
) -> None:
    """Boot the chat server.

    Args:
        host: Interface to bind (default loopback).
        port: TCP port (default 9120).
        token: Optional explicit bearer secret; when ``None`` (the usual
            case) the value is read from ``chatui.password`` in
            ``~/.hermes/config.yaml`` and one is generated + persisted on
            first run so the URL stays the same across restarts.
        open_browser: Open a browser tab on startup.
    """
    import uvicorn

    from . import state
    state._SESSION_TOKEN = token or _load_or_init_password()
    state._BOUND_HOST = host
    state._BOUND_PORT = port
    _load_auth_sessions()

    if logging.getLogger().level > logging.INFO:
        logging.basicConfig(level=logging.INFO)
    logger.setLevel(logging.INFO)

    url = f"http://{host}:{port}/?token={state._SESSION_TOKEN}"
    print(f"Hermes Chat: {url}")
    print("  Password is persisted at ~/.hermes/config.yaml under chatui.password — edit to change.")
    logger.info("Hermes Chat server starting on %s:%s", host, port)

    if open_browser:
        def _open() -> None:
            time.sleep(1.0)
            try:
                import webbrowser

                webbrowser.open(url)
            except Exception:
                logger.debug("could not open browser", exc_info=True)

        threading.Thread(target=_open, daemon=True).start()

    uvicorn.run(app, host=host, port=port, log_level="warning")
