"""Auth and PWA root asset routes."""

from __future__ import annotations

import hmac
import secrets
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

from ..state import (
    CHAT_STATIC_DIR,
    TOKEN_PLACEHOLDER,
    _AUTH_LOCK,
    _AUTH_SESSIONS,
    _SESSION_TOKEN,
    _check_bearer,
    _is_bearer_valid,
    _is_query_token_valid,
    _is_session_cookie_valid,
    _load_auth_sessions,
    _persist_auth_sessions,
    _rate_limited,
    _set_session_cookie,
)

router = APIRouter()


def _read_index_html() -> str:
    index = CHAT_STATIC_DIR / "index.html"
    if not index.is_file():
        # Friendly placeholder so a fresh checkout still boots.
        return (
            "<!doctype html><meta charset=\"utf-8\">"
            f"<meta name=\"hermes-token\" content=\"{TOKEN_PLACEHOLDER}\">"
            "<title>Hermes Chat</title>"
            "<h1>Hermes Chat backend is running.</h1>"
            "<p>The frontend (<code>hermes_cli/chat_static/index.html</code>) "
            "has not been built yet.</p>"
        )
    return index.read_text(encoding="utf-8")


def _read_login_html() -> str:
    p = CHAT_STATIC_DIR / "login.html"
    if not p.is_file():
        return (
            "<!doctype html><meta charset=\"utf-8\"><title>Hermes</title>"
            "<form method=\"post\" action=\"/api/login\">"
            "<input type=\"password\" name=\"password\" placeholder=\"Password\">"
            "<button>Sign in</button></form>"
        )
    return p.read_text(encoding="utf-8")


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    """Serve the chat shell when authenticated; otherwise the login page.

    The legacy ``?token=<password>`` URL form is still accepted: when it
    matches, set the cookie and continue. New PWA installs land on the
    login page instead.
    """
    qtoken = request.query_params.get("token")
    if qtoken and _is_query_token_valid(qtoken):
        sid = secrets.token_urlsafe(32)
        with _AUTH_LOCK:
            _AUTH_SESSIONS.add(sid)
        _persist_auth_sessions()
        html = _read_index_html()
        if TOKEN_PLACEHOLDER in html:
            html = html.replace(TOKEN_PLACEHOLDER, "")
        resp = HTMLResponse(content=html)
        _set_session_cookie(resp, request, sid)
        return resp

    if not (_is_session_cookie_valid(request) or _is_bearer_valid(request)):
        return HTMLResponse(content=_read_login_html(), status_code=200)

    html = _read_index_html()
    # Strip the placeholder — cookies handle auth, no JS-readable token needed.
    if TOKEN_PLACEHOLDER in html:
        html = html.replace(TOKEN_PLACEHOLDER, "")
    return HTMLResponse(content=html)


class _LoginBody(BaseModel):
    password: str


@router.post("/api/login")
async def api_login(body: _LoginBody, request: Request) -> JSONResponse:
    """Validate the password and set the auth cookie on success."""
    if _rate_limited():
        raise HTTPException(status_code=429, detail="too many attempts, slow down")
    pw = (body.password or "").strip()
    if not _SESSION_TOKEN or not pw or not hmac.compare_digest(
        pw.encode(), _SESSION_TOKEN.encode()
    ):
        raise HTTPException(status_code=401, detail="Wrong password")
    sid = secrets.token_urlsafe(32)
    with _AUTH_LOCK:
        _AUTH_SESSIONS.add(sid)
    _persist_auth_sessions()
    resp = JSONResponse({"ok": True})
    _set_session_cookie(resp, request, sid)
    return resp


@router.post("/api/logout")
async def api_logout(request: Request) -> JSONResponse:
    """Clear the cookie and drop this session id from the persistent set."""
    cookie = request.cookies.get("hermes_session", "")
    if cookie:
        with _AUTH_LOCK:
            _AUTH_SESSIONS.discard(cookie)
        _persist_auth_sessions()
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("hermes_session", path="/")
    return resp


def _root_static_file(name: str, media_type: Optional[str] = None) -> FileResponse:
    p = CHAT_STATIC_DIR / name
    if not p.is_file():
        raise HTTPException(status_code=404, detail=f"{name} not found")
    return FileResponse(p, media_type=media_type)


@router.get("/manifest.json")
async def manifest() -> FileResponse:
    return _root_static_file("manifest.json", "application/manifest+json")


@router.get("/sw.js")
async def service_worker() -> FileResponse:
    return _root_static_file("sw.js", "application/javascript")


@router.get("/favicon.ico")
async def favicon() -> FileResponse:
    return _root_static_file("favicon.ico", "image/x-icon")
