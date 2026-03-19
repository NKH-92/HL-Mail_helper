# MailAI Portable

Local mail workspace with AI-assisted triage, follow-up tracking, and portable desktop packaging.

## Run

```powershell
cd MailAI_Portable
pip install -r requirements.txt
python run_portable.py
```

Streamlit remains available as a compatibility/debug entry:

```powershell
streamlit run app/main.py
```

## Test

```powershell
python -m pytest -q
```

## Build

```powershell
python build/build_portable.py
```

## Source Layout

- `app/`: application source
- `app/ai/`: AI prompts, normalization, and ownership rules
- `app/core/`: config, scheduler, secrets, and shared utilities
- `app/db/`: SQLite schema, models, and repositories
- `app/mail/`: IMAP and SMTP integrations
- `app/services/`: sync, analysis, sending, and orchestration
- `app/ui/`: desktop bridge, Streamlit adapter, and shared UI helpers
- `app/ui/custom_board/`: desktop frontend HTML/CSS/JS
- `prompts/`: bundled fallback prompts
- `addressbook/`: bundled default address book CSV
- `tests/`: automated tests

## Architecture Notes

- Canonical UI payload shaping lives in `app/ui/ui_state_helpers.py`
- Desktop runtime entry is `run_portable.py`
- Streamlit compatibility entry is `app/main.py`
- See `docs/ARCHITECTURE.md` for the edit map and workflow notes
- See `docs/USER_MANUAL.md` for the end-user and operator manual
- See `AGENTS.md` for repo-local Codex guidance

Runtime directories such as `config/`, `data/`, `logs/`, `cache/`, and `templates/` are created locally as needed.
