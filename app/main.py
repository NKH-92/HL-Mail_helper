"""Streamlit entrypoint for MailAI Portable."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

CODE_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else CODE_ROOT
if str(CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT))

from app.core.security import MAIL_PASSWORD_KEY
from app.runtime_context import AppContext, build_app_context
from app.ui.modern_dashboard import render_modern_dashboard
from app.ui.page_config import (
    ALL_PAGES,
    DASHBOARD_PAGE,
    HELP_PAGE,
    SETTINGS_PAGE,
    TRAY_POPUP_QUERY_KEY,
    normalize_page as _normalize_page,
)
from app.ui.tray_popups import is_supported_popup_view, popup_page_title, render_tray_popup


def _set_current_page(page: str) -> None:
    """Persist page changes."""

    st.session_state["current_page"] = _normalize_page(page)


def _get_query_param(key: str) -> str | None:
    """Read one query-string value across supported Streamlit APIs."""

    try:
        value = st.query_params.get(key)
    except Exception:
        params = st.experimental_get_query_params()
        raw_value = params.get(key, [None])
        value = raw_value[0] if isinstance(raw_value, list) else raw_value

    if isinstance(value, list):
        value = value[0] if value else None
    if value is None:
        return None
    text = str(value).strip()
    return text or None


@st.cache_resource
def get_app_context() -> AppContext:
    """Create shared services once per Streamlit process."""

    return build_app_context(DATA_ROOT, CODE_ROOT)


def main() -> None:
    """Render the Streamlit application."""

    popup_mode = _get_query_param(TRAY_POPUP_QUERY_KEY)
    st.set_page_config(
        page_title=popup_page_title(popup_mode),
        layout="wide",
        initial_sidebar_state="collapsed",
    )

    context = get_app_context()
    context.address_book_service.sync_user_profile(context.config_manager)
    if is_supported_popup_view(popup_mode):
        render_tray_popup(context, popup_mode)
        return

    config = context.address_book_service.merge_config_profile(context.config_manager.load())
    is_ready = config.is_ready() and context.secret_store.has_secret(MAIL_PASSWORD_KEY)

    current_page = _normalize_page(st.session_state.get("current_page"))
    default_page = DASHBOARD_PAGE if is_ready else SETTINGS_PAGE
    if current_page not in ALL_PAGES:
        _set_current_page(default_page)
        current_page = default_page
    elif not is_ready and current_page not in {SETTINGS_PAGE, HELP_PAGE}:
        _set_current_page(SETTINGS_PAGE)
        current_page = SETTINGS_PAGE
    else:
        st.session_state["current_page"] = current_page

    render_modern_dashboard(context, current_page)


if __name__ == "__main__":
    main()
