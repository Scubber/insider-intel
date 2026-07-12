"""Ensure insider-intel API (:8000) + static UI (:5500) are running, then open the UI.

Default: open OPEN_UI.md in Cursor (with a one-click Simple Browser command link).
Use --browser for the OS default browser, or --no-browser to only start servers.
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
API_PORT = 8000
WEB_PORT = 5500
API_URL = f"http://127.0.0.1:{API_PORT}/health"
WEB_URL = f"http://127.0.0.1:{WEB_PORT}/"
OPEN_UI_MD = ROOT / "OPEN_UI.md"
PYTHON = sys.executable


def _port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex((host, port)) == 0


def _spawn(args: list[str], *, cwd: Path, log_name: str) -> None:
    log_dir = ROOT / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / log_name
    handle = log_path.open("a", encoding="utf-8")
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
    subprocess.Popen(
        args,
        cwd=str(cwd),
        stdout=handle,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        creationflags=creationflags,
        close_fds=True,
    )


def _wait_port(port: int, *, timeout: float = 12.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _port_open(port):
            return True
        time.sleep(0.25)
    return _port_open(port)


def _cursor_bin() -> str | None:
    env = os.environ.get("CURSOR_BIN") or os.environ.get("VSCODE_BIN")
    if env and Path(env).is_file():
        return env
    found = shutil.which("cursor")
    if found:
        return found
    win = (
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Programs"
        / "cursor"
        / "resources"
        / "app"
        / "bin"
        / "cursor.cmd"
    )
    if win.is_file():
        return str(win)
    return None


def open_in_cursor_editor(url: str = WEB_URL) -> str:
    """Open helper markdown in Cursor so the user can one-click Simple Browser.

    Direct CLI → Simple Browser is unreliable on Cursor desktop; opening OPEN_UI.md
    (with a ``command:simpleBrowser.show`` link) is the reliable path.
    """
    cursor = _cursor_bin()
    if not OPEN_UI_MD.is_file():
        return "missing-open-ui-md"
    if not cursor:
        return "no-cursor-cli"

    # Prefer reusing the current window and focusing OPEN_UI.md
    try:
        subprocess.run(
            [cursor, "--reuse-window", "-g", str(OPEN_UI_MD)],
            check=False,
            timeout=20,
            capture_output=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "cursor-goto-failed"

    # Also try the Simple Browser / integrated browser URI (best-effort; often no-op)
    encoded = quote(url, safe="")
    for uri in (
        f"vscode://vscode.simple-browser/show?url={encoded}",
        f"cursor://vscode.simple-browser/show?url={encoded}",
    ):
        try:
            subprocess.run(
                [cursor, "--reuse-window", "--open-url", uri],
                check=False,
                timeout=10,
                capture_output=True,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass

    return "open-ui-md"


def open_ui(*, target: str = "cursor") -> str:
    """Open the UI. target: cursor | browser | none."""
    if target == "none":
        return "none"
    if target == "browser":
        webbrowser.open(WEB_URL)
        return "browser"
    return open_in_cursor_editor(WEB_URL)


def ensure_servers(*, open_target: str = "cursor") -> dict[str, object]:
    started_api = False
    started_web = False

    if not _port_open(API_PORT):
        _spawn(
            [PYTHON, "-m", "apps.search", "serve"],
            cwd=ROOT,
            log_name="api.log",
        )
        started_api = True
        if not _wait_port(API_PORT):
            return {
                "ok": False,
                "error": f"API did not bind :{API_PORT}",
                "web_url": WEB_URL,
            }

    if not _port_open(WEB_PORT):
        _spawn(
            [PYTHON, "-m", "http.server", str(WEB_PORT), "--directory", "web"],
            cwd=ROOT,
            log_name="web.log",
        )
        started_web = True
        if not _wait_port(WEB_PORT):
            return {
                "ok": False,
                "error": f"Web server did not bind :{WEB_PORT}",
                "web_url": WEB_URL,
            }

    opened = open_ui(target=open_target)

    return {
        "ok": True,
        "started_api": started_api,
        "started_web": started_web,
        "opened": opened,
        "api_url": API_URL,
        "web_url": WEB_URL,
        "open_ui_md": str(OPEN_UI_MD),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Launch insider-intel local UI + API.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--no-browser",
        action="store_true",
        help="Start servers without opening editor or browser",
    )
    group.add_argument(
        "--browser",
        action="store_true",
        help="Open in the OS default browser",
    )
    group.add_argument(
        "--cursor",
        action="store_true",
        help="Open OPEN_UI.md in Cursor for one-click Simple Browser (default)",
    )
    args = parser.parse_args(argv)
    os.environ.setdefault("PYTHONPATH", str(ROOT))

    if args.no_browser:
        target = "none"
    elif args.browser:
        target = "browser"
    else:
        target = "cursor"

    result = ensure_servers(open_target=target)
    if not result.get("ok"):
        print(result.get("error") or "launch failed", file=sys.stderr)
        return 1

    print(
        f"insider-intel ready -> {result['web_url']} "
        f"(api={'started' if result['started_api'] else 'already up'}, "
        f"web={'started' if result['started_web'] else 'already up'}, "
        f"open={result.get('opened')})"
    )
    if result.get("opened") == "open-ui-md":
        print(
            "Opened OPEN_UI.md - click 'Open in Cursor Browser' in that file, "
            "or Ctrl+Shift+P -> Tasks: Run Task -> insider-intel: open in Cursor Browser"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
