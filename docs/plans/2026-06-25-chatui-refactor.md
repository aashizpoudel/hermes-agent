# ChatUI Refactor: Module Split + Frontend Build

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Split the 1,762-line `chat_server.py` monolith into focused modules, and replace CDN-loaded Tailwind/marked with a bundled build + syntax highlighting.

**Architecture:** Backend splits into `hermes_cli/chat_server/` package with route modules + AgentRunner + media utils. Frontend gets a `hermes_cli/chat_static/` build system using esbuild for JS bundling and Tailwind CLI for CSS compilation.

**Tech Stack:** Python (FastAPI routers), esbuild (JS bundling), Tailwind CLI (CSS), highlight.js (syntax highlighting), marked (markdown).

---

## Part A: Backend Module Split

### Task A1: Create chat_server package structure

**Objective:** Convert `hermes_cli/chat_server.py` file into a package directory.

**Files:**
- Create: `hermes_cli/chat_server/__init__.py`
- Move: `hermes_cli/chat_server.py` → `hermes_cli/chat_server/_legacy.py` (temporary)

**Step 1: Create directory and empty `__init__.py`**

```bash
mkdir -p hermes_cli/chat_server
```

```python
# hermes_cli/chat_server/__init__.py
"""Hermes Chat — FastAPI backend for the PWA chat frontend."""
```

**Step 2: Move the old file as reference**

```bash
mv hermes_cli/chat_server.py hermes_cli/chat_server/_legacy.py
```

**Step 3: Update import in main.py**

Search for `from hermes_cli.chat_server import` or `hermes_cli.chat_server` in `hermes_cli/main.py` and verify the import path still works. The package `__init__.py` will re-export everything initially.

**Step 4: Verify import works**

```bash
cd /home/amilab/codes/hermes-agent
python -c "from hermes_cli.chat_server import app; print('OK')"
```

**Step 5: Commit**

```bash
git add hermes_cli/chat_server/ hermes_cli/main.py
git commit -m "refactor(chatui): convert chat_server.py to package"
```

---

### Task A2: Extract shared state + utilities

**Objective:** Pull module-level state, constants, and utility functions into a dedicated module.

**Files:**
- Create: `hermes_cli/chat_server/state.py`
- Create: `hermes_cli/chat_server/media.py`

**Step 1: Create `state.py` with all module-level state**

Move these from `_legacy.py`:
- `CHAT_STATIC_DIR`, `TOKEN_PLACEHOLDER`, `_MEDIA_RE`
- `_SESSION_TOKEN`, `_BOUND_HOST`, `_BOUND_PORT`
- `_STREAMS`, `_CANCELS`, `_FILE_TOKENS`, `_FILE_TOKENS_LOCK`
- `_uploads_dir()`, `_register_file()`, `_resolve_file()`
- `COOKIE_NAME`, `_AUTH_SESSIONS`, `_AUTH_LOCK`, `_LOGIN_ATTEMPTS`, `_LOGIN_RATE_LIMIT`, `_LOGIN_RATE_WINDOW`
- `_auth_state_path()`, `_load_auth_sessions()`, `_persist_auth_sessions()`
- `_is_session_cookie_valid()`, `_is_bearer_valid()`, `_is_query_token_valid()`, `_check_auth()`, `_check_token_query_or_header()`
- `_is_request_secure()`, `_set_session_cookie()`, `_rate_limited()`

```python
# hermes_cli/chat_server/state.py
"""Shared state, constants, and auth helpers for the chat server."""
from __future__ import annotations
import hmac
import logging
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()

CHAT_STATIC_DIR = Path(__file__).parent.parent / "chat_static"
TOKEN_PLACEHOLDER = "<!--HERMES_TOKEN-->"

_MEDIA_RE = re.compile(
    r'''[`"']?MEDIA:\s*(?P<path>`[^\`\n]+`|"[^"\n]+"|'[^'\n]+'|(?:~/|/)\S+(?:[^\S\n]+\S+)*?\.(?:png|jpe?g|gif|webp|mp4|mov|avi|mkv|webm|ogg|opus|mp3|wav|m4a|epub|pdf|zip|rar|7z|docx?|xlsx?|pptx?|txt|csv|apk|ipa)(?=[\s`"',;:)\]}]|$)|\S+)[`"']?'''
)

_SESSION_TOKEN: str = ""
_BOUND_HOST: str = "127.0.0.1"
_BOUND_PORT: int = 9120

_STREAMS: Dict[str, asyncio.Queue] = {}
_CANCELS: Dict[str, threading.Event] = {}
_FILE_TOKENS: Dict[str, Path] = {}
_FILE_TOKENS_LOCK = threading.Lock()

COOKIE_NAME = "hermes_session"
_AUTH_SESSIONS: Set[str] = set()
_AUTH_LOCK = threading.Lock()
_LOGIN_ATTEMPTS: List[float] = []
_LOGIN_RATE_LIMIT = 5
_LOGIN_RATE_WINDOW = 30.0

# ... paste all the helper functions from _legacy.py lines 126-824 ...
```

**Step 2: Create `media.py` with MEDIA: rewriting**

```python
# hermes_cli/chat_server/media.py
"""MEDIA: path rewriting for file attachments."""
from __future__ import annotations
import logging
import os
import re
import shutil
import uuid
from pathlib import Path

from .state import _MEDIA_RE, _uploads_dir, _register_file

logger = logging.getLogger(__name__)


def rewrite_media(text: str, session_id: str) -> str:
    """Replace ``MEDIA:/abs/path`` substrings with ``/files/<token>`` URLs."""
    # ... paste _rewrite_media from _legacy.py lines 643-672 ...
```

**Step 3: Verify imports**

```bash
python -c "from hermes_cli.chat_server.state import _STREAMS; print('state OK')"
python -c "from hermes_cli.chat_server.media import rewrite_media; print('media OK')"
```

**Step 4: Commit**

```bash
git add hermes_cli/chat_server/state.py hermes_cli/chat_server/media.py
git commit -m "refactor(chatui): extract state.py and media.py from monolith"
```

---

### Task A3: Extract AgentRunner

**Objective:** Move the `AgentRunner` class into its own module.

**Files:**
- Create: `hermes_cli/chat_server/runner.py`

**Step 1: Create `runner.py`**

Move the `AgentRunner` class (lines 149-640 from `_legacy.py`) plus the `_rewrite_media` reference. Import state from `state.py` and media from `media.py`.

```python
# hermes_cli/chat_server/runner.py
"""AgentRunner — owns the AIAgent lifecycle and SSE event queue."""
from __future__ import annotations
import asyncio
import logging
import queue
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from .state import _STREAMS, _CANCELS, _FILE_TOKENS, _FILE_TOKENS_LOCK, _uploads_dir, _register_file
from .media import rewrite_media

logger = logging.getLogger(__name__)


class AgentRunner:
    # ... paste entire class from _legacy.py lines 149-640 ...
    pass
```

**Step 2: Verify**

```bash
python -c "from hermes_cli.chat_server.runner import AgentRunner; print('runner OK')"
```

**Step 3: Commit**

```bash
git add hermes_cli/chat_server/runner.py
git commit -m "refactor(chatui): extract AgentRunner into runner.py"
```

---

### Task A4: Extract route modules

**Objective:** Split API endpoints into focused route modules.

**Files:**
- Create: `hermes_cli/chat_server/routes/__init__.py`
- Create: `hermes_cli/chat_server/routes/auth.py`
- Create: `hermes_cli/chat_server/routes/sessions.py`
- Create: `hermes_cli/chat_server/routes/models.py`
- Create: `hermes_cli/chat_server/routes/messages.py`
- Create: `hermes_cli/chat_server/routes/files.py`
- Create: `hermes_cli/chat_server/routes/commands.py`

**Step 1: Create routes package**

```bash
mkdir -p hermes_cli/chat_server/routes
```

```python
# hermes_cli/chat_server/routes/__init__.py
```

**Step 2: Create `routes/auth.py`**

Move from `_legacy.py`: `_read_index_html()`, `_read_login_html()`, `index()`, `_LoginBody`, `api_login()`, `api_logout()` (lines 830-921).

Use FastAPI APIRouter:

```python
# hermes_cli/chat_server/routes/auth.py
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

router = APIRouter()

@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    ...

@router.post("/api/login")
async def api_login(body, request: Request) -> JSONResponse:
    ...

@router.post("/api/logout")
async def api_logout(request: Request) -> JSONResponse:
    ...
```

**Step 3: Create `routes/messages.py`**

Move: `MessageBody`, `post_message()`, `_sse_iter()`, `stream()`, `cancel()` (lines 958-1094).

```python
# hermes_cli/chat_server/routes/messages.py
from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

router = APIRouter()

@router.post("/api/message")
async def post_message(body: MessageBody, request: Request):
    ...

@router.get("/api/stream/{stream_id}")
async def stream(stream_id: str, request: Request, token: ...):
    ...

@router.post("/api/cancel/{stream_id}")
async def cancel(stream_id: str, request: Request):
    ...
```

**Step 4: Create `routes/files.py`**

Move: `upload()`, `serve_file()` (lines 1114-1147).

**Step 5: Create `routes/sessions.py`**

Move: `_SessionIdBody`, `_SessionRenameBody`, `_session_meta_dict()`, `list_sessions()`, `new_session()`, `switch_session()`, `_replay_messages()`, `rename_session()`, `delete_session()` (lines 1150-1368).

**Step 6: Create `routes/models.py`**

Move: `_ModelSwitchBody`, `_trim_provider()`, `_load_provider_args()`, `list_providers()`, `list_models()`, `current_model()`, `switch_model_endpoint()` (lines 1371-1625).

**Step 7: Create `routes/commands.py`**

Move: `_HELP_TEXT`, `command()` (lines 1628-1673).

**Step 8: Verify all routes import**

```bash
python -c "
from hermes_cli.chat_server.routes.auth import router as r
from hermes_cli.chat_server.routes.messages import router as r
from hermes_cli.chat_server.routes.sessions import router as r
from hermes_cli.chat_server.routes.models import router as r
from hermes_cli.chat_server.routes.files import router as r
from hermes_cli.chat_server.routes.commands import router as r
print('All route modules OK')
"
```

**Step 9: Commit**

```bash
git add hermes_cli/chat_server/routes/
git commit -m "refactor(chatui): split API endpoints into route modules"
```

---

### Task A5: Wire up the app in `__init__.py`

**Objective:** Assemble the FastAPI app from the extracted modules.

**Files:**
- Modify: `hermes_cli/chat_server/__init__.py`
- Delete: `hermes_cli/chat_server/_legacy.py`

**Step 1: Write the app assembly**

```python
# hermes_cli/chat_server/__init__.py
"""Hermes Chat — FastAPI backend for the PWA chat frontend."""
from __future__ import annotations
import logging
import os
import secrets
import sys
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# --- FastAPI app ---
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .state import CHAT_STATIC_DIR, _load_auth_sessions, _SESSION_TOKEN, _BOUND_HOST, _BOUND_PORT
from .runner import AgentRunner
from .routes.auth import router as auth_router
from .routes.messages import router as messages_router
from .routes.sessions import router as sessions_router
from .routes.models import router as models_router
from .routes.files import router as files_router
from .routes.commands import router as commands_router

app = FastAPI(title="Hermes Chat", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(messages_router)
app.include_router(sessions_router)
app.include_router(models_router)
app.include_router(files_router)
app.include_router(commands_router)

# Static assets
if CHAT_STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(CHAT_STATIC_DIR)), name="chat-static")

# PWA root assets
@app.get("/manifest.json")
async def manifest():
    ...

@app.get("/sw.js")
async def service_worker():
    ...

@app.get("/favicon.ico")
async def favicon():
    ...

@app.get("/api/health")
async def health():
    ...

# Module-level runner (lazy init)
_runner: Optional[AgentRunner] = None

def get_runner() -> AgentRunner:
    global _runner
    if _runner is None:
        _runner = AgentRunner()
    return _runner

# ... start_server(), _load_or_init_password() ...
```

**Step 2: Update all route modules to import `_runner` via `get_runner()`**

Each route that references `_runner` should import it:

```python
from ..runner import get_runner
_runner = get_runner  # or call get_runner() in each endpoint
```

Actually, better pattern — use a module-level reference that gets set once:

```python
# In __init__.py, after creating the runner:
from . import state
state._runner = AgentRunner()
```

Or use FastAPI dependency injection:

```python
# routes/messages.py
from fastapi import Depends
from ..runner import AgentRunner

def get_runner():
    from .. import _runner
    return _runner

@router.post("/api/message")
async def post_message(body: MessageBody, request: Request, runner: AgentRunner = Depends(get_runner)):
    ...
```

**Step 3: Verify the full app loads**

```bash
python -c "from hermes_cli.chat_server import app; print(f'Routes: {len(app.routes)}')"
```

**Step 4: Delete the legacy file**

```bash
rm hermes_cli/chat_server/_legacy.py
```

**Step 5: Smoke test — boot the server**

```bash
cd /home/amilab/codes/hermes-agent
timeout 5 python -c "from hermes_cli.chat_server import start_server; start_server(open_browser=False)" 2>&1 || true
```

**Step 6: Commit**

```bash
git add hermes_cli/chat_server/
git commit -m "refactor(chatui): assemble app from extracted modules, delete monolith"
```

---

## Part B: Frontend Build + Syntax Highlighting

### Task B1: Set up build tooling

**Objective:** Add esbuild + Tailwind CLI + highlight.js to the project.

**Files:**
- Create: `hermes_cli/chat_static/package.json`
- Create: `hermes_cli/chat_static/tailwind.config.js`
- Create: `hermes_cli/chat_static/src/app.js` (move from root)
- Create: `hermes_cli/chat_static/src/style.css` (move from root)

**Step 1: Create package.json**

```json
{
  "name": "hermes-chatui",
  "private": true,
  "scripts": {
    "build": "npm run build:css && npm run build:js",
    "build:js": "esbuild src/app.js --bundle --outfile=dist/app.js --minify --target=es2020 --format=iife",
    "build:css": "npx tailwindcss -i src/style.css -o dist/style.css --minify",
    "watch": "concurrently \"npm run watch:js\" \"npm run watch:css\"",
    "watch:js": "esbuild src/app.js --bundle --outfile=dist/app.js --watch --target=es2020 --format=iife",
    "watch:css": "npx tailwindcss -i src/style.css -o dist/style.css --watch"
  },
  "devDependencies": {
    "esbuild": "^0.21.0",
    "tailwindcss": "^3.4.0",
    "@tailwindcss/typography": "^0.5.0",
    "concurrently": "^8.0.0"
  },
  "dependencies": {
    "marked": "^14.0.0",
    "highlight.js": "^11.9.0"
  }
}
```

**Step 2: Create `tailwind.config.js`**

```js
/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['../chat_static/index.html', '../chat_static/src/app.js'],
  theme: {
    extend: {
      colors: {
        accent: {
          DEFAULT: '#7c3aed',
          500: '#7c3aed',
          600: '#6d28d9',
          400: '#8b5cf6',
        },
      },
      fontFamily: {
        sans: ['-apple-system', 'BlinkMacSystemFont', 'Inter', 'SF Pro Text', 'system-ui', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      maxWidth: {
        'col': '48rem',
      },
    },
  },
  plugins: [require('@tailwindcss/typography')],
};
```

**Step 3: Move source files**

```bash
mkdir -p hermes_cli/chat_static/src hermes_cli/chat_static/dist
mv hermes_cli/chat_static/app.js hermes_cli/chat_static/src/app.js
mv hermes_cli/chat_static/style.css hermes_cli/chat_static/src/style.css
```

**Step 4: Install dependencies**

```bash
cd hermes_cli/chat_static && npm install
```

**Step 5: Commit**

```bash
git add hermes_cli/chat_static/package.json hermes_cli/chat_static/tailwind.config.js hermes_cli/chat_static/src/ hermes_cli/chat_static/dist/
git commit -m "feat(chatui): add build tooling (esbuild, tailwind, highlight.js)"
```

---

### Task B2: Bundle marked + add highlight.js

**Objective:** Import marked and highlight.js in app.js instead of loading from CDN.

**Files:**
- Modify: `hermes_cli/chat_static/src/app.js`

**Step 1: Add imports at top of app.js**

```js
// hermes_cli/chat_static/src/app.js
import { marked } from 'marked';
import hljs from 'highlight.js/lib/core';
import javascript from 'highlight.js/lib/languages/javascript';
import python from 'highlight.js/lib/languages/python';
import bash from 'highlight.js/lib/languages/bash';
import json from 'highlight.js/lib/languages/json';
import yaml from 'highlight.js/lib/languages/yaml';
import markdown from 'highlight.js/lib/languages/markdown';
import xml from 'highlight.js/lib/languages/xml';
import css from 'highlight.js/lib/languages/css';
import typescript from 'highlight.js/lib/languages/typescript';
import rust from 'highlight.js/lib/languages/rust';
import go from 'highlight.js/lib/languages/go';
import java from 'highlight.js/lib/languages/java';
import c from 'highlight.js/lib/languages/c';
import cpp from 'highlight.js/lib/languages/cpp';
import sql from 'highlight.js/lib/languages/sql';
import diff from 'highlight.js/lib/languages/diff';

hljs.registerLanguage('javascript', javascript);
hljs.registerLanguage('js', javascript);
hljs.registerLanguage('python', python);
hljs.registerLanguage('py', python);
hljs.registerLanguage('bash', bash);
hljs.registerLanguage('sh', bash);
hljs.registerLanguage('shell', bash);
hljs.registerLanguage('json', json);
hljs.registerLanguage('yaml', yaml);
hljs.registerLanguage('yml', yaml);
hljs.registerLanguage('markdown', markdown);
hljs.registerLanguage('md', markdown);
hljs.registerLanguage('xml', xml);
hljs.registerLanguage('html', xml);
hljs.registerLanguage('css', css);
hljs.registerLanguage('typescript', typescript);
hljs.registerLanguage('ts', typescript);
hljs.registerLanguage('rust', rust);
hljs.registerLanguage('rs', rust);
hljs.registerLanguage('go', go);
hljs.registerLanguage('java', java);
hljs.registerLanguage('c', c);
hljs.registerLanguage('cpp', cpp);
hljs.registerLanguage('sql', sql);
hljs.registerLanguage('diff', diff);
```

**Step 2: Replace `renderMarkdown` to use marked + hljs**

```js
function renderMarkdown(text) {
  if (!text) return '';
  marked.setOptions({
    breaks: true,
    gfm: true,
    highlight: function(code, lang) {
      if (lang && hljs.getLanguage(lang)) {
        try { return hljs.highlight(code, { language: lang }).value; }
        catch (_) {}
      }
      try { return hljs.highlightAuto(code).value; }
      catch (_) {}
      return code;
    },
  });
  return marked.parse(text);
}
```

**Step 3: Remove CDN script tags from index.html**

In `index.html`, remove:
```html
<script src="https://cdn.tailwindcss.com?plugins=typography"></script>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
```

Replace with:
```html
<link rel="stylesheet" href="/static/dist/style.css">
```

And add before `</body>`:
```html
<script src="/static/dist/app.js"></script>
```

**Step 4: Add highlight.js CSS theme**

Add to `src/style.css`:
```css
@import 'highlight.js/styles/github.css';
```

Or create a separate theme file and import it.

**Step 5: Build and verify**

```bash
cd hermes_cli/chat_static
npm run build
ls -la dist/
```

**Step 6: Commit**

```bash
git add hermes_cli/chat_static/
git commit -m "feat(chatui): bundle marked + highlight.js, remove CDN deps"
```

---

### Task B3: Update service worker for bundled assets

**Objective:** Update sw.js to cache the new dist/ files.

**Files:**
- Modify: `hermes_cli/chat_static/sw.js`

**Step 1: Update SHELL array**

```js
const CACHE_VERSION = 'hermes-v10-bundled';
const SHELL = ['/static/dist/app.js', '/static/dist/style.css', '/manifest.json', '/static/favicon.svg'];
```

**Step 2: Bump cache version**

Already done above (`hermes-v10-bundled`).

**Step 3: Commit**

```bash
git add hermes_cli/chat_static/sw.js
git commit -m "feat(chatui): update service worker for bundled assets"
```

---

### Task B4: Update server to serve dist/ files

**Objective:** Ensure the FastAPI server serves the new dist/ directory.

**Files:**
- Modify: `hermes_cli/chat_server/__init__.py` (or wherever static mount lives)

**Step 1: Verify the static mount covers dist/**

The existing mount:
```python
app.mount("/static", StaticFiles(directory=str(CHAT_STATIC_DIR)), name="chat-static")
```

This already serves everything under `chat_static/`, including `dist/`. So `/static/dist/app.js` works automatically.

**Step 2: No changes needed — verify**

```bash
curl -s http://localhost:9120/static/dist/style.css | head -5
```

**Step 3: Commit (if any changes needed)**

```bash
git commit --allow-empty -m "chore(chatui): verify dist/ serving works"
```

---

### Task B5: Add highlight.js CSS to the build

**Objective:** Bundle the highlight.js theme into the CSS output.

**Files:**
- Modify: `hermes_cli/chat_static/src/style.css`

**Step 1: Add import at top of style.css**

```css
/* Highlight.js theme — github style for code blocks */
@import 'highlight.js/styles/github.css';

/* Hermes — only what Tailwind can't do well: keyframes, SSE max-height transitions, scrollbar polish. */
/* ... existing CSS ... */
```

**Step 2: Rebuild CSS**

```bash
cd hermes_cli/chat_static && npm run build:css
```

**Step 3: Verify code blocks are styled**

Check that `dist/style.css` contains hljs rules:
```bash
grep -c 'hljs' hermes_cli/chat_static/dist/style.css
```

**Step 4: Commit**

```bash
git add hermes_cli/chat_static/
git commit -m "feat(chatui): bundle highlight.js theme into CSS"
```

---

## Verification

### Full smoke test

```bash
cd /home/amilab/codes/hermes-agent

# Verify Python imports
python -c "
from hermes_cli.chat_server import app
print(f'App loaded: {app.title}')
print(f'Routes: {len(app.routes)}')
"

# Verify frontend build
cd hermes_cli/chat_static
npm run build
ls -la dist/
# Should see: app.js (~30-50KB minified), style.css (~15-25KB minified)

# Boot the server (if model is configured)
cd /home/amilab/codes/hermes-agent
timeout 10 python -m hermes_cli.chat_server 2>&1 || true
```

### What the user sees

- Same chat UI, same features
- Faster page load (no CDN fetches)
- Code blocks now have syntax highlighting
- Offline support still works (SW caches dist/ files)
- All slash commands, session management, model picker unchanged

---

## File count summary

| Action | Files |
|--------|-------|
| Created | `chat_server/__init__.py`, `chat_server/state.py`, `chat_server/media.py`, `chat_server/runner.py`, `chat_server/routes/{__init__,auth,messages,sessions,models,files,commands}.py`, `chat_static/package.json`, `chat_static/tailwind.config.js`, `chat_static/src/app.js`, `chat_static/src/style.css` |
| Modified | `chat_static/index.html`, `chat_static/sw.js` |
| Deleted | `chat_server.py` (replaced by package) |
| Created (build) | `chat_static/dist/app.js`, `chat_static/dist/style.css` |
