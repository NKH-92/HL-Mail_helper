from __future__ import annotations

import unittest
from pathlib import Path

from app.ui import page_config


class MainUiRoutingTests(unittest.TestCase):
    def test_primary_pages_route_through_custom_shell(self) -> None:
        main_path = Path(__file__).resolve().parents[1] / "app" / "main.py"
        main_source = main_path.read_text(encoding="utf-8")

        self.assertIn("render_modern_dashboard(context, current_page)", main_source)
        self.assertNotIn("render_dashboard(", main_source)
        self.assertNotIn("render_templates_page(", main_source)
        self.assertNotIn("render_logs_page(", main_source)
        self.assertNotIn("render_settings_page(", main_source)

    def test_help_page_is_registered_in_shared_page_config(self) -> None:
        self.assertIn(page_config.HELP_PAGE, page_config.ALL_PAGES)
        self.assertEqual(page_config.resolve_page_id(page_config.HELP_PAGE), "help")

    def test_archive_and_completed_pages_are_registered_in_shared_page_config(self) -> None:
        self.assertIn(page_config.ARCHIVE_PAGE, page_config.ALL_PAGES)
        self.assertIn(page_config.COMPLETED_PAGE, page_config.ALL_PAGES)
        self.assertEqual(page_config.resolve_page_id(page_config.ARCHIVE_PAGE), "archive")
        self.assertEqual(page_config.resolve_page_id(page_config.COMPLETED_PAGE), "completed")

    def test_main_allows_help_page_before_setup(self) -> None:
        main_path = Path(__file__).resolve().parents[1] / "app" / "main.py"
        main_source = main_path.read_text(encoding="utf-8")

        self.assertIn("current_page not in {SETTINGS_PAGE, HELP_PAGE}", main_source)

    def test_ui_patch_falls_back_to_page_label_when_page_id_is_missing(self) -> None:
        ui_patch_path = Path(__file__).resolve().parents[1] / "app" / "ui" / "custom_board" / "ui_patch.js"
        ui_patch_source = ui_patch_path.read_text(encoding="utf-8")

        self.assertIn('const PAGE_ID_BY_LABEL = {', ui_patch_source)
        self.assertIn('"도움말": "help"', ui_patch_source)
        self.assertIn("const pageId = resolvePageId(safeArgs);", ui_patch_source)

    def test_help_nav_uses_button_markup_for_keyboard_access(self) -> None:
        index_path = Path(__file__).resolve().parents[1] / "app" / "ui" / "custom_board" / "index.html"
        index_source = index_path.read_text(encoding="utf-8")

        self.assertIn('<button type="button" onclick="navigate(\'도움말\')" data-page="도움말"', index_source)

    def test_archive_and_completed_nav_use_button_markup_for_keyboard_access(self) -> None:
        index_path = Path(__file__).resolve().parents[1] / "app" / "ui" / "custom_board" / "index.html"
        index_source = index_path.read_text(encoding="utf-8")

        self.assertIn('<button type="button" onclick="navigate(\'보관함\')" data-page="보관함"', index_source)
        self.assertIn('<button type="button" onclick="navigate(\'완료\')" data-page="완료"', index_source)


if __name__ == "__main__":
    unittest.main()
