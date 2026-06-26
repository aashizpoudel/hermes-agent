"""MEDIA: rewriter — copy referenced files into uploads dir and rewrite to /files/<token>."""

from __future__ import annotations

import logging
import os
import re
import shutil
import uuid
from pathlib import Path

from .state import _MEDIA_RE, _register_file, _uploads_dir

logger = logging.getLogger(__name__)


def _rewrite_media(text: str, session_id: str) -> str:
    """Replace ``MEDIA:/abs/path`` substrings with ``/files/<token>`` URLs.

    The file is copied into the uploads dir for this session so the token
    map points at a stable location even if the original is cleaned up.
    """
    if not text or "MEDIA:" not in text:
        return text

    out_dir = _uploads_dir(session_id)

    def _sub(match: "re.Match[str]") -> str:
        raw = match.group("path").strip()
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "`\"'":
            raw = raw[1:-1].strip()
        raw = raw.lstrip("`\"'").rstrip("`\"',.;:)}]")
        src = Path(os.path.expanduser(raw))
        if not src.is_absolute() or not src.is_file():
            return match.group(0)  # leave unrecognised entries alone
        try:
            ext = src.suffix or ""
            dst = out_dir / f"{uuid.uuid4().hex}{ext}"
            shutil.copy2(src, dst)
            token = _register_file(dst)
            return f"/files/{token}"
        except Exception:
            logger.warning("MEDIA rewrite failed for %s", src, exc_info=True)
            return match.group(0)

    return _MEDIA_RE.sub(_sub, text)
