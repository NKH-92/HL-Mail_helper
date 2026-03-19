from __future__ import annotations

import unittest

from app.ui import tray_popups


class TrayPopupHelpersTests(unittest.TestCase):
    def test_popup_page_title_matches_known_views(self) -> None:
        self.assertEqual(tray_popups.popup_page_title(tray_popups.POPUP_TODO_VIEW), "MailAI | 내 할일")
        self.assertEqual(tray_popups.popup_page_title(tray_popups.POPUP_AUTO_SEND_VIEW), "MailAI | 자동발송")
        self.assertEqual(tray_popups.popup_page_title("unknown"), "MailAI Portable")

    def test_supported_popup_views_are_guarded(self) -> None:
        self.assertTrue(tray_popups.is_supported_popup_view(tray_popups.POPUP_TODO_VIEW))
        self.assertTrue(tray_popups.is_supported_popup_view(tray_popups.POPUP_AUTO_SEND_VIEW))
        self.assertFalse(tray_popups.is_supported_popup_view("invalid"))

    def test_due_chip_marks_missing_deadline_as_muted(self) -> None:
        label, badge_class = tray_popups._due_chip(None)

        self.assertEqual(label, "기한 없음")
        self.assertEqual(badge_class, "is-muted")


if __name__ == "__main__":
    unittest.main()
