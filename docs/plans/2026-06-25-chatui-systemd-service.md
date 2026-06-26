# ChatUI Systemd Service Management Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Add `hermes chatui start/stop/status/install/uninstall` commands that manage a systemd user service for the chatui PWA frontend.

**Architecture:** Create a new `hermes_cli/chatui_service.py` module that handles systemd unit generation, installation, and lifecycle — mirroring the gateway's service management pattern but simpler (no system-scope, no legacy units, no s6). Modify `cmd_chatui` to dispatch to subcommands.

**Tech Stack:** Python, systemd (user scope), argparse subparsers

---

## Task 1: Create `hermes_cli/chatui_service.py` with service constants

**Objective:** Create the service module with constants and helper functions.

**Files:**
- Create: `hermes_cli/chatui_service.py`

**Step 1: Create the module**

```python
"""Systemd user service management for Hermes ChatUI."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

SERVICE_NAME = "hermes-chatui"
SERVICE_DESCRIPTION = "Hermes ChatUI — PWA frontend for Hermes Agent"
SERVICE_UNIT_DIR = Path.home() / ".config" / "systemd" / "user"


def _get_python_path() -> str:
    """Return the Python executable path (prefer venv)."""
    venv_python = Path(sys.prefix) / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def _get_hermes_home() -> str:
    """Return HERMES_HOME, defaulting to ~/.hermes."""
    return os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))


def _get_unit_path() -> Path:
    """Return the systemd unit file path."""
    return SERVICE_UNIT_DIR / f"{SERVICE_NAME}.service"


def _run_systemctl(args: list[str], check: bool = True, timeout: int = 30) -> subprocess.CompletedProcess:
    """Run systemctl --user with given args."""
    cmd = ["systemctl", "--user"] + args
    return subprocess.run(cmd, check=check, timeout=timeout, capture_output=True, text=True)
```

**Step 2: Verify it imports**

Run: `python -c "from hermes_cli.chatui_service import SERVICE_NAME; print(SERVICE_NAME)"`
Expected: `hermes-chatui`

**Step 3: Commit**

```bash
git add hermes_cli/chatui_service.py
git commit -m "feat(chatui): add systemd service management module"
```

---

## Task 2: Add unit file generation

**Objective:** Generate a systemd user unit file for chatui.

**Files:**
- Modify: `hermes_cli/chatui_service.py`

**Step 1: Add generate function**

```python
def generate_unit(host: str = "0.0.0.0", port: int = 9120) -> str:
    """Generate systemd user unit file content."""
    python_path = _get_python_path()
    hermes_home = _get_hermes_home()
    working_dir = str(Path.home() / "codes" / "hermes-agent")

    return f"""[Unit]
Description={SERVICE_DESCRIPTION}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={python_path} -m hermes_cli.main chatui --host {host} --port {port} --no-open
WorkingDirectory={working_dir}
Environment="HERMES_HOME={hermes_home}"
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
"""
```

**Step 2: Commit**

```bash
git add hermes_cli/chatui_service.py
git commit -m "feat(chatui): add unit file generation"
```

---

## Task 3: Add install/uninstall functions

**Objective:** Write unit file to systemd user directory and manage daemon-reload.

**Files:**
- Modify: `hermes_cli/chatui_service.py`

**Step 1: Add install and uninstall functions**

```python
def install(host: str = "0.0.0.0", port: int = 9120, force: bool = False, enable: bool = True) -> None:
    """Install the chatui systemd user service."""
    unit_path = _get_unit_path()

    if unit_path.exists() and not force:
        print(f"Service already installed at: {unit_path}")
        print("Use --force to reinstall")
        return

    SERVICE_UNIT_DIR.mkdir(parents=True, exist_ok=True)
    unit_content = generate_unit(host=host, port=port)
    unit_path.write_text(unit_content, encoding="utf-8")
    print(f"✓ Installed service to: {unit_path}")

    _run_systemctl(["daemon-reload"], check=True)
    if enable:
        _run_systemctl(["enable", SERVICE_NAME], check=True)
        print(f"✓ Service enabled (auto-start on login)")

    print()
    print("Next steps:")
    print(f"  hermes chatui start              # Start the service")
    print(f"  hermes chatui status             # Check status")
    print(f"  journalctl --user -u {SERVICE_NAME} -f  # View logs")
    print()


def uninstall() -> None:
    """Uninstall the chatui systemd user service."""
    _run_systemctl(["stop", SERVICE_NAME], check=False, timeout=10)
    _run_systemctl(["disable", SERVICE_NAME], check=False)

    unit_path = _get_unit_path()
    if unit_path.exists():
        unit_path.unlink()
        print(f"✓ Removed {unit_path}")

    _run_systemctl(["daemon-reload"], check=True)
    print(f"✓ Service uninstalled")
```

**Step 2: Commit**

```bash
git add hermes_cli/chatui_service.py
git commit -m "feat(chatui): add install/uninstall service functions"
```

---

## Task 4: Add start/stop/status functions

**Objective:** Manage the service lifecycle.

**Files:**
- Modify: `hermes_cli/chatui_service.py`

**Step 1: Add lifecycle functions**

```python
def start() -> None:
    """Start the chatui service."""
    if not _get_unit_path().exists():
        print("Service not installed. Run: hermes chatui install")
        sys.exit(1)
    _run_systemctl(["start", SERVICE_NAME], check=True)
    print(f"✓ {SERVICE_NAME} started")


def stop() -> None:
    """Stop the chatui service."""
    _run_systemctl(["stop", SERVICE_NAME], check=True)
    print(f"✓ {SERVICE_NAME} stopped")


def status() -> None:
    """Show chatui service status."""
    result = _run_systemctl(["status", SERVICE_NAME], check=False)
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr)
```

**Step 2: Commit**

```bash
git add hermes_cli/chatui_service.py
git commit -m "feat(chatui): add start/stop/status service functions"
```

---

## Task 5: Modify `cmd_chatui` to support subcommands

**Objective:** Change chatui from a simple command to a subcommand dispatcher.

**Files:**
- Modify: `hermes_cli/main.py` (cmd_chatui function + parser registration)

**Step 1: Replace cmd_chatui function**

Find the existing `cmd_chatui` function (around line 11504) and replace with:

```python
def cmd_chatui(args):
    """ChatUI management commands."""
    subcmd = getattr(args, "chatui_command", None)

    if subcmd is None or subcmd == "run":
        # Original foreground behavior
        try:
            import fastapi  # noqa: F401
            import uvicorn  # noqa: F401
        except ImportError as e:
            print("Chat UI dependencies not installed (need fastapi + uvicorn).")
            print(
                f"Install with:\n  {sys.executable} -m pip install 'fastapi' 'uvicorn[standard]'"
            )
            print(f"Import error: {e}")
            sys.exit(1)

        from hermes_cli.chat_server import start_server

        start_server(
            host=args.host,
            port=args.port,
            token=None,
            open_browser=not args.no_open,
        )
        return

    from hermes_cli import chatui_service

    if subcmd == "install":
        host = getattr(args, "host", "0.0.0.0")
        port = getattr(args, "port", 9120)
        force = getattr(args, "force", False)
        chatui_service.install(host=host, port=port, force=force)
    elif subcmd == "uninstall":
        chatui_service.uninstall()
    elif subcmd == "start":
        chatui_service.start()
    elif subcmd == "stop":
        chatui_service.stop()
    elif subcmd == "status":
        chatui_service.status()
    else:
        print(f"Unknown chatui subcommand: {subcmd}")
        sys.exit(1)
```

**Step 2: Update the parser registration**

Find the chatui parser section (around line 13165) and replace with:

```python
    # =========================================================================
    # chatui command — FastAPI chat backend + PWA frontend
    # =========================================================================
    chatui_parser = subparsers.add_parser(
        "chatui",
        help="Manage the Hermes Chat web UI",
        description="Launch or manage the Hermes Chat PWA frontend",
    )
    chatui_sub = chatui_parser.add_subparsers(dest="chatui_command")

    # hermes chatui run (default) — foreground mode
    run_parser = chatui_sub.add_parser("run", help="Run chatui in foreground (default)")
    run_parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default 127.0.0.1)")
    run_parser.add_argument("--port", type=int, default=9120, help="Port to bind (default 9120)")
    run_parser.add_argument("--no-open", action="store_true", help="Don't open the browser automatically")
    run_parser.set_defaults(func=cmd_chatui)

    # hermes chatui install
    install_parser = chatui_sub.add_parser("install", help="Install systemd user service")
    install_parser.add_argument("--host", default="0.0.0.0", help="Host to bind (default 0.0.0.0)")
    install_parser.add_argument("--port", type=int, default=9120, help="Port to bind (default 9120)")
    install_parser.add_argument("--force", action="store_true", help="Force reinstall")
    install_parser.set_defaults(func=cmd_chatui)

    # hermes chatui uninstall
    uninstall_parser = chatui_sub.add_parser("uninstall", help="Remove systemd user service")
    uninstall_parser.set_defaults(func=cmd_chatui)

    # hermes chatui start
    start_parser = chatui_sub.add_parser("start", help="Start the service")
    start_parser.set_defaults(func=cmd_chatui)

    # hermes chatui stop
    stop_parser = chatui_sub.add_parser("stop", help="Stop the service")
    stop_parser.set_defaults(func=cmd_chatui)

    # hermes chatui status
    status_parser = chatui_sub.add_parser("status", help="Show service status")
    status_parser.set_defaults(func=cmd_chatui)

    # Default to run if no subcommand
    chatui_parser.set_defaults(func=cmd_chatui, chatui_command="run")
```

**Step 3: Commit**

```bash
git add hermes_cli/main.py
git commit -m "feat(chatui): add start/stop/install/uninstall subcommands"
```

---

## Task 6: Install dependencies automatically on service start

**Objective:** Check and install fastapi/uvicorn before starting the service.

**Files:**
- Modify: `hermes_cli/chatui_service.py`

**Step 1: Add dependency check function**

```python
def _ensure_dependencies() -> None:
    """Install chatui dependencies if missing."""
    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401
    except ImportError:
        print("Installing chatui dependencies...")
        subprocess.check_call([
            sys.executable, "-m", "pip", "install", "fastapi", "uvicorn[standard]"
        ])
        print("✓ Dependencies installed")
```

**Step 2: Call it in install()**

Add `_ensure_dependencies()` at the start of the `install()` function.

**Step 3: Commit**

```bash
git add hermes_cli/chatui_service.py
git commit -m "feat(chatui): auto-install dependencies on service setup"
```

---

## Task 7: Test the full flow

**Objective:** Verify end-to-end service management.

**Step 1: Install the service**

Run: `hermes chatui install`
Expected: Service installed, enabled, instructions printed

**Step 2: Check status**

Run: `hermes chatui status`
Expected: Service inactive/dead (not started yet)

**Step 3: Start the service**

Run: `hermes chatui start`
Expected: Service started

**Step 4: Verify it's running**

Run: `curl -s http://127.0.0.1:9120/ | head -5`
Expected: HTML response from chatui

**Step 5: Stop the service**

Run: `hermes chatui stop`
Expected: Service stopped

**Step 6: Uninstall**

Run: `hermes chatui uninstall`
Expected: Service removed

**Step 7: Test foreground mode still works**

Run: `hermes chatui run --port 9121`
Expected: Chatui starts in foreground on port 9121

**Step 8: Final commit**

```bash
git add -A
git commit -m "test(chatui): verify systemd service management flow"
```

---

## Summary

After implementation, users can:

```bash
hermes chatui install    # Install + enable systemd service
hermes chatui start      # Start the service
hermes chatui stop       # Stop the service
hermes chatui status     # Show status
hermes chatui uninstall  # Remove the service
hermes chatui run        # Run in foreground (original behavior)
```

The service auto-starts on login, auto-restarts on failure, and logs to journalctl.
