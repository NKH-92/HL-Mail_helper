# MailAI Portable Architecture

## Runtime map

MailAI currently ships with two UI entry paths that share the same application services.

1. Desktop runtime
   - entry: `run_portable.py`
   - shell: `pywebview`
   - JS/Python bridge: `app/ui/desktop_bridge.py`
   - primary frontend: `app/ui/custom_board/index.html` + `app/ui/custom_board/ui_patch.js`

2. Streamlit compatibility runtime
   - entry: `app/main.py`
   - renderer: `app/ui/modern_dashboard.py`
   - used as fallback/debug path, not the main desktop experience

Both runtimes build services from `app/runtime_context.py`.

Mailbox auto-sync timing is config-driven through `AppConfig.sync_interval_minutes`.
Persisted sync progress such as `last_sync_at` and backfill cursor lives in `app_settings`,
while the next scheduled run and latest in-memory cycle result come from `SchedulerManager`.
AI runtime selection is config-driven through `AppConfig.ai_provider`, `AppConfig.ai_base_url`,
and `AppConfig.gemini_model`, while actual AI secrets live only in OS keyring entries managed
by `app/core/security.py`.

## Source of truth by area

- Service wiring: `app/runtime_context.py`
- Config, security, scheduler, address book: `app/core/`
- SQLite models and repositories: `app/db/`
- Mail sync and send orchestration: `app/services/`
- AI prompts and normalization: `app/ai/`
- Shared UI payload shaping: `app/ui/ui_state_helpers.py`
- Desktop bridge state/actions: `app/ui/desktop_bridge.py`
- Streamlit adapter: `app/ui/modern_dashboard.py`
- Frontend assets: `app/ui/custom_board/`

## Coding rules for future changes

### Dashboard field changes

If you add or rename a dashboard field:

1. Update `app/ui/ui_state_helpers.py`.
2. Update `app/ui/custom_board/ui_patch.js` and, if needed, `app/ui/custom_board/index.html`.
3. Keep `app/ui/modern_dashboard.py` aligned with the same shared payload helpers.
4. Run:
   - `node --check app/ui/custom_board/ui_patch.js`
   - `python -m pytest -q`

### Mail analysis changes

If you change AI extraction or classification behavior:

1. Update prompts in `app/ai/prompts.py` when schema expectations change.
2. Update normalization and deterministic fallback logic in `app/ai/analyzer.py`.
3. Keep repository persistence expectations aligned in `app/db/repositories.py`.
4. Add or update tests under `tests/test_analyzer_normalization.py` and related repository tests.

### Ranking or follow-up behavior

Thread priority and follow-up labels are determined in `app/db/repositories.py`.
UI rendering should only display the already-ranked result, not re-implement ranking decisions.

## Generated folders

These are not source-of-truth and should usually be ignored during development:

- `build/`
- `dist/`
- `release/`
- `cache/`
- `data/`
- `logs/`
- `__pycache__/`

## Useful commands

```powershell
python -m pytest -q
node --check app/ui/custom_board/ui_patch.js
python run_portable.py
streamlit run app/main.py
python build/build_portable.py
```
