"""Module-level state, constants, and auth helpers for the chat server."""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from fastapi import HTTPException, Request

from hermes_cli.config import get_hermes_home

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

CHAT_STATIC_DIR = Path(__file__).parent.parent / "chat_static"
TOKEN_PLACEHOLDER = "<!--HERMES_TOKEN-->"

# MEDIA: pattern lifted from gateway/platforms/base.py::extract_media so
# the agent's TTS/image tool output renders inline.
_MEDIA_RE = re.compile(
    r'''[`"']?MEDIA:\s*(?P<path>`[^\`\n]+`|"[^"\n]+"|'[^'\n]+'|(?:~/|/)\S+(?:[^\S\n]+\S+)*?\.(?:png|jpe?g|gif|webp|mp4|mov|avi|mkv|webm|ogg|opus|mp3|wav|m4a|epub|pdf|zip|rar|7z|docx?|xlsx?|pptx?|txt|csv|apk|ipa)(?=[\s`"',;:)}\]]|$)|\S+)[`"']?'''
)

_SESSION_TOKEN: str = ""
_BOUND_HOST: str = "127.0.0.1"
_BOUND_PORT: int = 9120

# stream_id -> asyncio.Queue of event dicts
_STREAMS: Dict[str, asyncio.Queue] = {}
# stream_id -> threading.Event for soft-cancel
_CANCELS: Dict[str, threading.Event] = {}

# file token -> absolute Path (uploads + MEDIA: rewrites)
_FILE_TOKENS: Dict[str, Path] = {}
_FILE_TOKENS_LOCK = threading.Lock()


def _uploads_dir(session_id: str) -> Path:
    p = get_hermes_home() / "uploads" / session_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def _register_file(path: Path) -> str:
    token = secrets.token_urlsafe(24)
    with _FILE_TOKENS_LOCK:
        _FILE_TOKENS[token] = path
    return token


def _resolve_file(token: str) -> Optional[Path]:
    with _FILE_TOKENS_LOCK:
        return _FILE_TOKENS.get(token)


# ---------------------------------------------------------------------------
# Auth state and helpers
# ---------------------------------------------------------------------------

COOKIE_NAME = "hermes_session"
_AUTH_SESSIONS: Set[str] = set()
_AUTH_LOCK = threading.Lock()
_LOGIN_ATTEMPTS: List[float] = []
_LOGIN_RATE_LIMIT = 5  # attempts
_LOGIN_RATE_WINDOW = 30.0  # seconds


def _auth_state_path() -> Path:
    """Persistent location for the login-session set (one ID per line)."""
    try:
        from hermes_constants import get_hermes_home

        return get_hermes_home() / ".chatui_sessions"
    except Exception:  # noqa: BLE001
        return Path.home() / ".hermes" / ".chatui_sessions"


def _load_auth_sessions() -> None:
    """Load persisted session IDs into the in-memory set on startup."""
    p = _auth_state_path()
    if not p.is_file():
        return
    try:
        with _AUTH_LOCK:
            for line in p.read_text(encoding="utf-8").splitlines():
                tok = line.strip()
                if tok:
                    _AUTH_SESSIONS.add(tok)
    except Exception as e:  # noqa: BLE001
        logger.warning("could not load auth sessions: %s", e)


def _persist_auth_sessions() -> None:
    """Write the in-memory session set to disk so logins survive restarts."""
    p = _auth_state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with _AUTH_LOCK:
            payload = "\n".join(sorted(_AUTH_SESSIONS)) + "\n" if _AUTH_SESSIONS else ""
        tmp = p.with_suffix(".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, p)
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
    except Exception as e:  # noqa: BLE001
        logger.warning("could not persist auth sessions: %s", e)


def _is_session_cookie_valid(request: Request) -> bool:
    cookie = request.cookies.get(COOKIE_NAME, "")
    if not cookie:
        return False
    with _AUTH_LOCK:
        return cookie in _AUTH_SESSIONS


def _is_bearer_valid(request: Request) -> bool:
    """Accept ``Authorization: Bearer *** for curl/scripts."""
    auth = request.headers.get("authorization", "")
    expected = f"Bearer {_SESSION_TOKEN}"
    return bool(_SESSION_TOKEN) and hmac.compare_digest(
        auth.encode(), expected.encode()
    )


def _is_query_token_valid(token: Optional[str]) -> bool:
    """Legacy ``?token=<password>`` fallback — kept for old PWA installs."""
    if not token or not _SESSION_TOKEN:
        return False
    return hmac.compare_digest(token.encode(), _SESSION_TOKEN.encode())


def _check_auth(request: Request) -> None:
    """Validate the request via cookie OR bearer header. Raise 401 otherwise."""
    if _is_session_cookie_valid(request) or _is_bearer_valid(request):
        return
    raise HTTPException(status_code=401, detail="Unauthorized")


# Back-compat alias — older code paths in this module call _check_bearer.
_check_bearer = _check_auth


def _check_token_query_or_header(request: Request, token: Optional[str]) -> None:
    """For endpoints called by EventSource / <img>, which can't set headers.

    Cookies are auto-sent on same-origin requests, so the cookie check
    handles the modern path; the query/header forms remain for legacy
    PWA installs that still bake ``?token=`` into URLs.
    """
    if (
        _is_session_cookie_valid(request)
        or _is_bearer_valid(request)
        or _is_query_token_valid(token)
    ):
        return
    raise HTTPException(status_code=401, detail="Unauthorized")


def _is_request_secure(request: Request) -> bool:
    """Detect HTTPS even when behind a Traefik / reverse-proxy TLS terminator."""
    if request.url.scheme == "https":
        return True
    fwd = request.headers.get("x-forwarded-proto", "").lower().split(",")[0].strip()
    return fwd == "https"


def _set_session_cookie(resp, request: Request, value: str) -> None:
    resp.set_cookie(
        COOKIE_NAME,
        value=value,
        max_age=60 * 60 * 24 * 30,  # 30 days
        httponly=True,
        samesite="lax",
        secure=_is_request_secure(request),
        path="/",
    )


def _rate_limited() -> bool:
    """Sliding window: at most _LOGIN_RATE_LIMIT attempts per window."""
    now = time.time()
    cutoff = now - _LOGIN_RATE_WINDOW
    with _AUTH_LOCK:
        _LOGIN_ATTEMPTS[:] = [t for t in _LOGIN_ATTEMPTS if t >= cutoff]
        if len(_LOGIN_ATTEMPTS) >= _LOGIN_RATE_LIMIT:
            return True
        _LOGIN_ATTEMPTS.append(now)
        return False
