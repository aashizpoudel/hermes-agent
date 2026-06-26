"""Systemd user service management for Hermes ChatUI."""

from __future__ import annotations

import logging
import os
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


def _get_working_dir() -> str:
    """Return the working directory for the service."""
    # Use the hermes-agent source checkout
    hermes_home = Path(_get_hermes_home())
    # Try to find the source from the venv
    venv_parent = Path(sys.prefix).parent.parent
    if (venv_parent / "hermes_cli").exists():
        return str(venv_parent)
    # Fallback to common locations
    for candidate in [
        Path.home() / "codes" / "hermes-agent",
        Path.home() / "hermes-agent",
    ]:
        if candidate.exists():
            return str(candidate)
    return str(hermes_home)


def _run_systemctl(
    args: list[str], check: bool = True, timeout: int = 30
) -> subprocess.CompletedProcess:
    """Run systemctl --user with given args."""
    cmd = ["systemctl", "--user"] + args
    return subprocess.run(cmd, check=check, timeout=timeout, capture_output=True, text=True)


def _ensure_dependencies() -> None:
    """Install chatui dependencies if missing."""
    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401
    except ImportError:
        print("Installing chatui dependencies (fastapi + uvicorn)...")
        # Try uv first (uv-managed venvs have no pip module)
        import shutil
        uv_bin = shutil.which("uv")
        if uv_bin:
            subprocess.check_call(
                [uv_bin, "pip", "install", "fastapi", "uvicorn[standard]"]
            )
        else:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "fastapi", "uvicorn[standard]"]
            )
        print("✓ Python dependencies installed")

    # Install npm dependencies if node_modules is missing in chat_static
    chat_static_dir = Path(__file__).parent / "chat_static"
    if chat_static_dir.exists() and not (chat_static_dir / "node_modules").exists():
        print("Installing chatui npm dependencies...")
        subprocess.check_call(
            ["npm", "install"], cwd=str(chat_static_dir)
        )
        print("✓ npm dependencies installed")


def generate_unit(host: str = "0.0.0.0", port: int = 9120) -> str:
    """Generate systemd user unit file content."""
    python_path = _get_python_path()
    hermes_home = _get_hermes_home()
    working_dir = _get_working_dir()

    return f"""[Unit]
Description={SERVICE_DESCRIPTION}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={python_path} -m hermes_cli.main chatui run --host {host} --port {port} --no-open
WorkingDirectory={working_dir}
Environment="HERMES_HOME={hermes_home}"
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
"""


def install(host: str = "0.0.0.0", port: int = 9120, force: bool = False) -> None:
    """Install the chatui systemd user service."""
    _ensure_dependencies()

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
    _run_systemctl(["enable", SERVICE_NAME], check=True)
    print("✓ Service enabled (auto-start on login)")

    print()
    print("Next steps:")
    print("  hermes chatui start              # Start the service")
    print("  hermes chatui status             # Check status")
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
    print("✓ Service uninstalled")


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
