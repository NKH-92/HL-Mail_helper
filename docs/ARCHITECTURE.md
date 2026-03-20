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
Portable runtime state is stored under the sibling `cache/` folder next to the executable.
That folder now owns `config/`, `data/`, `logs/`, `templates/`, `addressbook/`, and browser cache data.
Default AI prompt text is built into the application binary; `cache/prompts/` is only an optional override path.

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

### Dashboard classification flow

The main dashboard now uses a mail-classification payload, not the old thread/task board.

- Canonical backend query: `MailRepository.list_classified_mails()`
- Shared payload builder: `build_classified_mail_dicts()` in `app/ui/ui_state_helpers.py`
- Tray popups should mirror the same classification dataset and must not fall back to legacy open-action-item queries.
- Supported tabs:
  - `category_1`: direct action
  - `category_2`: review needed
  - `category_3`: reference only
- Client selection state:
  - `dashboard_mail_tab`
  - `dashboard_mail_view`
  - `selected_mail_id`
- Clicking a mail summary should switch the list panel into a summary-only detail view with a back button.

### Mail analysis changes

If you change AI extraction or classification behavior:

1. Update prompts in `app/ai/prompts.py` when schema expectations change.
2. Update normalization, validation audit, rule facts, and decision policy in `app/ai/classification_engine.py`.
3. Keep the compatibility re-export in `app/ai/analyzer.py` aligned if imports change.
4. Keep `app/services/analysis_service.py` aligned with the first-pass prompt contract and the conditional second-pass validator.
5. Keep repository persistence expectations aligned in `app/db/repositories.py`.
6. Keep `app/db/models.py` and `app/db/database.py` aligned when stored analysis fields change.
7. Add or update tests under `tests/test_analyzer_normalization.py`, `tests/test_prompt_and_ui_helpers.py`, and related service tests.

Current runtime flow:
- Rule engine computes routing facts and provisional `rule_category`.
- Rule engine may treat address-book personal aliases as the current user for `To`, while owned group or department aliases are treated as `CC`-level routing even when they appear in `To`.
- First-pass LLM prompt returns semantic fields only. It does not return `final_category`.
- Prompt input now preserves both the head and tail of long bodies to reduce missed tail-side requests.
- Thread context is built from recent message body excerpts, not only one-line summaries.
- `AnalysisService` may run a second-pass validator for low-confidence, short, thread-dependent, or rule-vs-LLM-conflict cases.
- Final category remains owned by `decide_final_category()` in `app/ai/classification_engine.py`.

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
