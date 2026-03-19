"""Shared page labels and routing helpers for the UI."""

from __future__ import annotations


DASHBOARD_PAGE = "메일 정리"
AUTO_SEND_PAGE = "메일 자동발송"
LEGACY_AUTO_SEND_PAGE = "템플릿 발송"
LOGS_PAGE = "로그"
SETTINGS_PAGE = "설정"
HELP_PAGE = "도움말"
TRAY_POPUP_QUERY_KEY = "popup"

PAGE_ALIASES = {
    LEGACY_AUTO_SEND_PAGE: AUTO_SEND_PAGE,
    "대시보드": DASHBOARD_PAGE,
}

ALL_PAGES = {
    DASHBOARD_PAGE,
    AUTO_SEND_PAGE,
    LOGS_PAGE,
    SETTINGS_PAGE,
    HELP_PAGE,
}


def normalize_page(page: str | None) -> str:
    """Translate legacy page names and guard against empty values."""

    if not page:
        return DASHBOARD_PAGE
    return PAGE_ALIASES.get(page, page)


def resolve_page_id(current_page: str) -> str:
    """Return the stable page id used by the frontend."""

    if current_page == DASHBOARD_PAGE:
        return "dashboard"
    if current_page == AUTO_SEND_PAGE:
        return "autosend"
    if current_page == LOGS_PAGE:
        return "logs"
    if current_page == SETTINGS_PAGE:
        return "settings"
    if current_page == HELP_PAGE:
        return "help"
    return "unknown"
