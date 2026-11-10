# JobHunter
Scheduled Python service: fetch India jobs from several sources, dedupe, score, email a digest.

## Stack
Python 3.12, SQLAlchemy 2 + Alembic, PostgreSQL (Neon), httpx, pydantic-settings,
pytest, ruff. Email via Resend. LLM via an LLMClient interface (Gemini default).

## Rules
- Every job source is a class in src/jobhunter/sources/ implementing SourceAdapter and returning RawJob. Never put source-specific code in pipeline.py.
- One failing source or LLM call must never stop a run. Catch, log to the runs table, continue.
- No network in tests. Use saved fixtures in tests/fixtures/.
- User settings (titles, experience range, cities, Gmail) live in the database and are edited in the web UI. Never hard-code them or read them from files.
- Never commit secrets. Read them from env via settings.py. Never print them.
- Never automate a logged-in LinkedIn account. LinkedIn data comes only from alert emails.
- Respect rate limits: Adzuna max 250 calls/day, Resend max 100 emails/day. Count calls in the database.
- Run `ruff check` and `pytest` before saying a task is done.
- Keep functions small; add type hints; dedupe via jobs.dedupe_key.

## Commands
- python -m jobhunter migrate | run | test-email
- pytest -q
