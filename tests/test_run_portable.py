from __future__ import annotations

import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

import run_portable


class RunPortableHelpersTests(unittest.TestCase):
    def test_parse_server_port_prefers_cli_override(self) -> None:
        port = run_portable._parse_server_port(["--streamlit-server", "--server-port=8517"])

        self.assertEqual(port, 8517)

    def test_parse_server_port_falls_back_to_default(self) -> None:
        port = run_portable._parse_server_port(["--streamlit-server"])

        self.assertEqual(port, run_portable.DEFAULT_PORT)

    def test_build_server_command_uses_script_in_dev_mode(self) -> None:
        runtime = run_portable.DesktopRuntime(
            bundle_root=Path.cwd(),
            data_root=Path.cwd(),
            app_path=Path.cwd() / "app" / "main.py",
        )

        with patch.object(run_portable.sys, "frozen", False, create=True):
            command = run_portable._build_server_command(runtime, 8520)

        self.assertEqual(command[0], sys.executable)
        self.assertEqual(Path(command[1]).name, "run_portable.py")
        self.assertIn("--streamlit-server", command)
        self.assertIn("--server-port=8520", command)

    def test_build_server_command_uses_executable_when_frozen(self) -> None:
        runtime = run_portable.DesktopRuntime(
            bundle_root=Path.cwd(),
            data_root=Path.cwd(),
            app_path=Path.cwd() / "app" / "main.py",
        )

        with patch.object(run_portable.sys, "frozen", True, create=True), patch.object(
            run_portable.sys, "executable", "C:\\MailAI\\MailAI_Portable.exe"
        ):
            command = run_portable._build_server_command(runtime, 8521)

        self.assertEqual(command, ["C:\\MailAI\\MailAI_Portable.exe", "--streamlit-server", "--server-port=8521"])

    def test_streamlit_flag_options_bind_local_server_and_disable_usage_stats(self) -> None:
        flags = run_portable._streamlit_flag_options(8530)

        self.assertTrue(flags["server_headless"])
        self.assertEqual(flags["server_port"], 8530)
        self.assertEqual(flags["server_address"], "127.0.0.1")
        self.assertFalse(flags["browser_gatherUsageStats"])

    def test_build_popup_url_adds_popup_and_refresh_query(self) -> None:
        popup_url = run_portable._build_popup_url("http://127.0.0.1:8501", run_portable.TRAY_TODO_POPUP)

        parsed = urlsplit(popup_url)
        query = parse_qs(parsed.query)
        self.assertEqual(f"{parsed.scheme}://{parsed.netloc}{parsed.path}", "http://127.0.0.1:8501")
        self.assertEqual(query[run_portable.TRAY_POPUP_QUERY_KEY], [run_portable.TRAY_TODO_POPUP])
        self.assertIn(run_portable.TRAY_REFRESH_QUERY_KEY, query)

    def test_build_popup_url_preserves_existing_query_values(self) -> None:
        popup_url = run_portable._build_popup_url(
            "http://127.0.0.1:8501/?foo=bar&popup=old",
            run_portable.TRAY_AUTO_SEND_POPUP,
        )

        query = parse_qs(urlsplit(popup_url).query)
        self.assertEqual(query["foo"], ["bar"])
        self.assertEqual(query[run_portable.TRAY_POPUP_QUERY_KEY], [run_portable.TRAY_AUTO_SEND_POPUP])

    def test_popup_window_specs_are_compact(self) -> None:
        todo_spec = run_portable.POPUP_WINDOW_SPECS[run_portable.TRAY_TODO_POPUP]
        autosend_spec = run_portable.POPUP_WINDOW_SPECS[run_portable.TRAY_AUTO_SEND_POPUP]

        self.assertEqual(todo_spec.title, "메일 분류")
        self.assertEqual(autosend_spec.title, "자동발송")
        self.assertEqual((todo_spec.width, todo_spec.height, todo_spec.min_size), (640, 720, (520, 480)))
        self.assertEqual((autosend_spec.width, autosend_spec.height, autosend_spec.min_size), (720, 760, (560, 520)))


if __name__ == "__main__":
    unittest.main()
