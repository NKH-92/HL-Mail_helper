"""Portable desktop launcher for MailAI."""

from __future__ import annotations

import ctypes
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from app.runtime_context import build_app_context
from app.ui.desktop_bridge import DesktopApi


APP_TITLE = "MailAI Portable"
DEFAULT_PORT = 8501
PORT_SCAN_LIMIT = 8
SERVER_START_TIMEOUT_SECONDS = 60.0
SERVER_POLL_INTERVAL_SECONDS = 0.25
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
TRAY_POPUP_QUERY_KEY = "popup"
TRAY_REFRESH_QUERY_KEY = "refresh"
TRAY_TODO_POPUP = "todos"
TRAY_AUTO_SEND_POPUP = "autosend"


@dataclass(slots=True)
class DesktopRuntime:
    bundle_root: Path
    data_root: Path
    app_path: Path


@dataclass(frozen=True, slots=True)
class PopupWindowSpec:
    title: str
    width: int
    height: int
    min_size: tuple[int, int]


POPUP_WINDOW_SPECS = {
    TRAY_TODO_POPUP: PopupWindowSpec(
        title="메일 분류",
        width=640,
        height=720,
        min_size=(520, 480),
    ),
    TRAY_AUTO_SEND_POPUP: PopupWindowSpec(
        title="자동발송",
        width=720,
        height=760,
        min_size=(560, 520),
    ),
}


class DesktopController:
    def __init__(self, runtime: DesktopRuntime, api: DesktopApi) -> None:
        self.runtime = runtime
        self.api = api
        self.main_window: Any | None = None
        self.popup_windows: dict[str, Any] = {}
        self.tray_icon: Any | None = None
        self._allow_main_window_close = False
        self._exit_requested = False
        self._tray_enabled = False
        self._lock = threading.RLock()

    def bind_main_window(self, window: Any) -> None:
        self.main_window = window
        window.events.closing += self._handle_main_window_closing
        window.events.closed += self._handle_main_window_closed

    def start_tray_icon(self) -> None:
        with self._lock:
            if self.tray_icon is not None:
                self._tray_enabled = True
                return

            try:
                import pystray
                tray_icon = pystray.Icon(
                    "mailai_portable",
                    _build_tray_icon_image(),
                    APP_TITLE,
                    pystray.Menu(
                        pystray.MenuItem("열기", self._on_tray_open, default=True),
                        pystray.MenuItem("메일 분류", self._on_tray_todos),
                        pystray.MenuItem("자동발송", self._on_tray_autosend),
                        pystray.MenuItem("종료", self._on_tray_exit),
                    ),
                )
                tray_icon.run_detached()
            except Exception as exc:  # pragma: no cover - packaging/runtime fallback
                _show_native_error(f"Unable to start the system tray icon.\n{exc}")
                self._tray_enabled = False
                return

            self.tray_icon = tray_icon
            self._tray_enabled = True

    def stop_tray_icon(self) -> None:
        with self._lock:
            tray_icon = self.tray_icon
            self.tray_icon = None
            self._tray_enabled = False

        if tray_icon is None:
            return

        try:
            tray_icon.stop()
        except Exception:
            pass

    def request_exit(self) -> None:
        with self._lock:
            if self._exit_requested:
                return
            self._exit_requested = True
            self._allow_main_window_close = True
            popup_windows = list(self.popup_windows.values())
            self.popup_windows.clear()
            main_window = self.main_window

        for popup_window in popup_windows:
            try:
                popup_window.destroy()
            except Exception:
                pass

        self.stop_tray_icon()
        try:
            self.api.context.scheduler_manager.shutdown()
        except Exception:
            pass

        if main_window is not None:
            try:
                main_window.destroy()
            except Exception:
                pass

    def show_main_window(self) -> None:
        window = self.main_window
        if window is None:
            return
        try:
            window.restore()
        except Exception:
            pass
        try:
            window.show()
        except Exception:
            pass

    def open_popup_window(self, popup_kind: str) -> None:
        popup_spec = POPUP_WINDOW_SPECS.get(popup_kind)
        if popup_spec is None:
            return

        popup_html = self.api.get_popup_html(popup_kind)
        with self._lock:
            existing_window = self.popup_windows.get(popup_kind)

        if existing_window is not None:
            try:
                existing_window.load_html(popup_html)
                existing_window.restore()
                existing_window.show()
                return
            except Exception:
                with self._lock:
                    self.popup_windows.pop(popup_kind, None)

        try:
            import webview
        except Exception:
            return

        popup_window = webview.create_window(
            popup_spec.title,
            html=popup_html,
            js_api=self.api,
            width=popup_spec.width,
            height=popup_spec.height,
            min_size=popup_spec.min_size,
            background_color="#f4f7fb",
            text_select=False,
            zoomable=False,
        )
        if popup_window is None:
            return
        popup_window.events.closed += lambda popup_kind=popup_kind: self._forget_popup_window(popup_kind)
        with self._lock:
            self.popup_windows[popup_kind] = popup_window

    def _forget_popup_window(self, popup_kind: str) -> None:
        with self._lock:
            self.popup_windows.pop(popup_kind, None)

    def _handle_main_window_closing(self) -> bool | None:
        if self._allow_main_window_close or not self._tray_enabled:
            return None

        if self.main_window is not None:
            try:
                self.main_window.hide()
            except Exception:
                return None

        return False

    def _handle_main_window_closed(self) -> None:
        if not self._exit_requested:
            self.stop_tray_icon()

    def _on_tray_open(self, icon: Any | None = None, item: Any | None = None) -> None:
        del icon, item
        self.show_main_window()

    def _on_tray_todos(self, icon: Any | None = None, item: Any | None = None) -> None:
        del icon, item
        self.open_popup_window(TRAY_TODO_POPUP)

    def _on_tray_autosend(self, icon: Any | None = None, item: Any | None = None) -> None:
        del icon, item
        self.open_popup_window(TRAY_AUTO_SEND_POPUP)

    def _on_tray_exit(self, icon: Any | None = None, item: Any | None = None) -> None:
        del icon, item
        self.request_exit()


def _resolve_runtime() -> DesktopRuntime:
    if getattr(sys, "frozen", False):
        bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
        data_root = Path(sys.executable).resolve().parent
    else:
        bundle_root = Path(__file__).resolve().parent
        data_root = bundle_root

    return DesktopRuntime(
        bundle_root=bundle_root,
        data_root=data_root,
        app_path=bundle_root / "app" / "main.py",
    )


def _parse_server_port(args: Sequence[str]) -> int:
    for item in args:
        if item.startswith("--server-port="):
            value = item.split("=", 1)[1].strip()
            if value.isdigit():
                return int(value)
    return DEFAULT_PORT


def _find_available_port(start_port: int = DEFAULT_PORT, scan_limit: int = PORT_SCAN_LIMIT) -> int:
    for port in range(start_port, start_port + scan_limit):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("No available port found for the embedded MailAI server.")


def _build_server_command(runtime: DesktopRuntime, port: int) -> list[str]:
    del runtime
    if getattr(sys, "frozen", False):
        return [sys.executable, "--streamlit-server", f"--server-port={port}"]
    return [sys.executable, str(Path(__file__).resolve()), "--streamlit-server", f"--server-port={port}"]


def _launch_server_process(runtime: DesktopRuntime, port: int) -> subprocess.Popen[str]:
    command = _build_server_command(runtime, port)
    popen_kwargs: dict[str, Any] = {
        "cwd": str(runtime.data_root),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = CREATE_NO_WINDOW
    return subprocess.Popen(command, **popen_kwargs)


def _streamlit_flag_options(port: int) -> dict[str, Any]:
    return {
        "server_headless": True,
        "server_port": port,
        "server_address": "127.0.0.1",
        "browser_gatherUsageStats": False,
        "global_developmentMode": False,
        "theme_base": "light",
        "theme_primaryColor": "#1d4ed8",
        "theme_backgroundColor": "#edf3f8",
        "theme_secondaryBackgroundColor": "#ffffff",
        "theme_textColor": "#0f172a",
    }


def _app_url(port: int) -> str:
    return f"http://127.0.0.1:{port}"


def _build_popup_url(base_url: str, popup_kind: str) -> str:
    parsed = urllib.parse.urlsplit(base_url)
    existing_pairs = [
        (key, value)
        for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if key not in {TRAY_POPUP_QUERY_KEY, TRAY_REFRESH_QUERY_KEY}
    ]
    existing_pairs.extend(
        [
            (TRAY_POPUP_QUERY_KEY, popup_kind),
            (TRAY_REFRESH_QUERY_KEY, str(time.time_ns())),
        ]
    )
    return urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(existing_pairs)))


def _wait_for_server(
    app_url: str,
    process: subprocess.Popen[str] | None = None,
    timeout_seconds: float = SERVER_START_TIMEOUT_SECONDS,
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    health_url = f"{app_url}/_stcore/health"

    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(health_url, timeout=0.5) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(SERVER_POLL_INTERVAL_SECONDS)
    return False


def _render_loading_html() -> str:
    return """
    <!doctype html>
    <html lang="en">
    <head>
      <meta charset="utf-8">
      <title>MailAI Portable</title>
      <style>
        :root {
          color-scheme: light;
          --bg: linear-gradient(135deg, #e8f1fb 0%, #f7fafc 52%, #eef4ff 100%);
          --panel: rgba(255, 255, 255, 0.92);
          --line: rgba(148, 163, 184, 0.24);
          --ink: #0f172a;
          --muted: #526175;
          --accent: #2563eb;
          --accent-soft: rgba(37, 99, 235, 0.12);
        }

        * { box-sizing: border-box; }

        body {
          margin: 0;
          min-height: 100vh;
          display: grid;
          place-items: center;
          background: var(--bg);
          font-family: "Segoe UI", "Malgun Gothic", sans-serif;
          color: var(--ink);
        }

        .shell {
          width: min(560px, calc(100vw - 48px));
          padding: 32px;
          border-radius: 28px;
          border: 1px solid var(--line);
          background: var(--panel);
          box-shadow: 0 30px 80px rgba(15, 23, 42, 0.12);
          backdrop-filter: blur(20px);
        }

        .eyebrow {
          display: inline-flex;
          align-items: center;
          gap: 10px;
          padding: 8px 12px;
          border-radius: 999px;
          background: var(--accent-soft);
          color: var(--accent);
          font-size: 13px;
          font-weight: 700;
          letter-spacing: 0.02em;
        }

        h1 {
          margin: 18px 0 10px;
          font-size: clamp(28px, 5vw, 38px);
          line-height: 1.08;
        }

        p {
          margin: 0;
          color: var(--muted);
          line-height: 1.65;
          font-size: 15px;
        }

        .status {
          margin-top: 22px;
          display: flex;
          align-items: center;
          gap: 14px;
        }

        .spinner {
          width: 18px;
          height: 18px;
          border-radius: 50%;
          border: 3px solid rgba(37, 99, 235, 0.16);
          border-top-color: var(--accent);
          animation: spin 0.9s linear infinite;
        }

        .hint {
          margin-top: 22px;
          padding-top: 18px;
          border-top: 1px solid var(--line);
          font-size: 13px;
          color: #64748b;
        }

        @keyframes spin {
          to { transform: rotate(360deg); }
        }
      </style>
    </head>
    <body>
      <main class="shell">
        <div class="eyebrow">MailAI Portable</div>
        <h1>Preparing the MailAI workspace.</h1>
        <p>The app is opening inside its own desktop window. Initial startup can take a few seconds while the local server becomes ready.</p>
        <div class="status">
          <div class="spinner" aria-hidden="true"></div>
          <strong>Starting embedded server</strong>
        </div>
        <div class="hint">When the interface finishes loading, keep using this window. No browser tab or manual URL entry is required.</div>
      </main>
    </body>
    </html>
    """


def _render_error_html(message: str) -> str:
    return f"""
    <!doctype html>
    <html lang="en">
    <head>
      <meta charset="utf-8">
      <title>MailAI Portable</title>
      <style>
        body {{
          margin: 0;
          min-height: 100vh;
          display: grid;
          place-items: center;
          background: #f8fafc;
          font-family: "Segoe UI", "Malgun Gothic", sans-serif;
          color: #0f172a;
        }}

        .shell {{
          width: min(560px, calc(100vw - 48px));
          padding: 30px;
          border-radius: 24px;
          border: 1px solid rgba(239, 68, 68, 0.22);
          background: #ffffff;
          box-shadow: 0 24px 60px rgba(15, 23, 42, 0.08);
        }}

        h1 {{
          margin: 0 0 12px;
          font-size: 30px;
        }}

        p {{
          margin: 0;
          line-height: 1.65;
          color: #475569;
        }}
      </style>
    </head>
    <body>
      <main class="shell">
        <h1>Unable to open the desktop window.</h1>
        <p>{message}</p>
      </main>
    </body>
    </html>
    """


def _show_native_error(message: str) -> None:
    if os.name == "nt":
        ctypes.windll.user32.MessageBoxW(0, message, APP_TITLE, 0x10)
        return
    print(message, file=sys.stderr)


def _build_tray_icon_image(size: int = 64) -> Any:
    try:
        from PIL import Image, ImageDraw
    except Exception as exc:  # pragma: no cover - packaging/runtime fallback
        raise RuntimeError("Pillow is required for tray icon rendering.") from exc

    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    outer_padding = int(size * 0.08)
    draw.rounded_rectangle(
        (outer_padding, outer_padding, size - outer_padding, size - outer_padding),
        radius=int(size * 0.22),
        fill=(37, 99, 235, 255),
    )
    mail_left = int(size * 0.2)
    mail_top = int(size * 0.26)
    mail_right = int(size * 0.8)
    mail_bottom = int(size * 0.72)
    draw.rounded_rectangle(
        (mail_left, mail_top, mail_right, mail_bottom),
        radius=int(size * 0.08),
        fill=(255, 255, 255, 255),
    )
    draw.line(
        (
            mail_left + 2,
            mail_top + 2,
            size // 2,
            int(size * 0.52),
            mail_right - 2,
            mail_top + 2,
        ),
        fill=(37, 99, 235, 255),
        width=max(2, size // 18),
    )
    return canvas


def _run_streamlit_server(runtime: DesktopRuntime, port: int) -> int:
    from streamlit.web import bootstrap

    flag_options = _streamlit_flag_options(port)
    bootstrap.load_config_options(flag_options=flag_options)
    bootstrap.run(
        str(runtime.app_path),
        False,
        [],
        flag_options,
    )
    return 0


def _run_desktop_shell(runtime: DesktopRuntime) -> int:
    try:
        import webview
    except Exception as exc:  # pragma: no cover - packaging/runtime fallback
        _show_native_error(f"Unable to load the desktop window runtime.\n{exc}")
        return 1

    try:
        context = build_app_context(runtime.data_root, runtime.bundle_root)
        api = DesktopApi(context)
    except Exception as exc:  # noqa: BLE001
        _show_native_error(f"Unable to initialize the MailAI runtime.\n{exc}")
        return 1

    controller = DesktopController(runtime, api)
    cache_root = runtime.data_root / "cache" / "webview"
    cache_root.mkdir(parents=True, exist_ok=True)
    window = webview.create_window(
        APP_TITLE,
        url=(runtime.bundle_root / "app" / "ui" / "custom_board" / "index.html").resolve().as_uri(),
        js_api=api,
        width=1540,
        height=980,
        min_size=(1180, 760),
        background_color="#edf3f8",
        text_select=False,
        zoomable=False,
    )
    api.bind_main_window(window)
    controller.bind_main_window(window)
    controller.start_tray_icon()

    try:
        webview.start(
            debug=False,
            private_mode=False,
            storage_path=str(cache_root),
        )
    finally:
        controller.stop_tray_icon()
        context.scheduler_manager.shutdown()

    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = list(argv or sys.argv[1:])
    runtime = _resolve_runtime()

    if "--streamlit-server" in args:
        return _run_streamlit_server(runtime, _parse_server_port(args))

    return _run_desktop_shell(runtime)


if __name__ == "__main__":
    raise SystemExit(main())
