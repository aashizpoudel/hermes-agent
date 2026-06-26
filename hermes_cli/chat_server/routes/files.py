"""File upload and serving routes."""

from __future__ import annotations

import logging
import mimetypes
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse

from ..runner import get_runner
from ..state import _check_bearer, _check_token_query_or_header, _register_file, _resolve_file, _uploads_dir

router = APIRouter()

logger = logging.getLogger(__name__)


@router.post("/api/upload")
async def upload(request: Request, file: UploadFile = File(...)) -> Dict[str, Any]:
    _check_bearer(request)
    runner = get_runner()
    out_dir = _uploads_dir(runner.session_id)
    ext = ""
    if file.filename and "." in file.filename:
        ext = "." + file.filename.rsplit(".", 1)[1].lower()
    dst = out_dir / f"{uuid.uuid4().hex}{ext}"
    try:
        with dst.open("wb") as fh:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                fh.write(chunk)
    finally:
        await file.close()
    token = _register_file(dst)
    return {
        "token": token,
        "url": f"/files/{token}",
        "filename": file.filename or dst.name,
    }


@router.get("/files/{token}")
async def serve_file(token: str, request: Request, t: Optional[str] = Query(None)):
    # Accept either Bearer header or ?t=<token> query
    _check_token_query_or_header(request, t)
    path = _resolve_file(token)
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    media_type, _ = mimetypes.guess_type(str(path))
    return FileResponse(path, media_type=media_type or "application/octet-stream")
