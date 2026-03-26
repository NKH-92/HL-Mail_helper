from __future__ import annotations

import unittest
from pathlib import Path


class CustomBoardAssetsTests(unittest.TestCase):
    def test_ui_patch_exports_toggle_task_handler(self) -> None:
        ui_patch = (
            Path(__file__).resolve().parents[1]
            / "app"
            / "ui"
            / "custom_board"
            / "ui_patch.js"
        ).read_text(encoding="utf-8")

        self.assertIn('onchange="window.toggleTask(', ui_patch)
        self.assertIn("window.toggleTask = toggleTask;", ui_patch)

    def test_ui_patch_marks_failed_analysis_cards_for_manual_review(self) -> None:
        ui_patch = (
            Path(__file__).resolve().parents[1]
            / "app"
            / "ui"
            / "custom_board"
            / "ui_patch.js"
        ).read_text(encoding="utf-8")

        self.assertIn("analysis_status", ui_patch)
        self.assertIn("analysis_error", ui_patch)
        self.assertIn("AI 재검토 필요", ui_patch)
        self.assertIn("분류 상태", ui_patch)


if __name__ == "__main__":
    unittest.main()
