# MailAI Portable Agent Notes

This file stays intentionally short. Keep only repo-wide, non-obvious rules here. Use `docs/ARCHITECTURE.md` for the full edit map, and add nested `AGENTS.md` files only when a subtree truly needs different guidance.

## High-Signal Facts

- Primary user-facing runtime: `run_portable.py`
- Shared service wiring: `app/runtime_context.py`
- Desktop path: `app/ui/desktop_bridge.py` plus `app/ui/custom_board/`
- Streamlit is a compatibility/debug path only: `app/main.py` plus `app/ui/modern_dashboard.py`
- AI extraction lives in `app/ai/prompts.py` and `app/ai/analyzer.py`
- Ranking and follow-up decisions live in `app/db/repositories.py`
- Packaging source is `build/build_portable.py`

## Stable Rules

- If a dashboard or thread payload changes, update `app/ui/ui_state_helpers.py` first. Then keep `app/ui/custom_board/`, `app/ui/desktop_bridge.py`, and `app/ui/modern_dashboard.py` aligned with the same payload and action names.
- UI code should render ranked and follow-up results, not reimplement ranking logic.
- Treat `build/MailAI_Portable/`, `dist/`, `release/`, `cache/`, `data/`, `logs/`, and `__pycache__/` as generated or runtime folders unless the task is explicitly about packaging or local data repair.
- If you change runtime flow or source-of-truth ownership, update `docs/ARCHITECTURE.md`.

## Validation

Run only the checks that match the change:

- Python behavior changes: `python -m pytest -q`
- `app/ui/custom_board/ui_patch.js` changes: `node --check app/ui/custom_board/ui_patch.js`
- Packaging changes: `python build/build_portable.py`
