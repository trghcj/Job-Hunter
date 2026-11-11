# JobHunter
Scheduled Python service: fetch India jobs from several sources, dedupe, score, email a digest.

## Stack
Python 3.12, SQLAlchemy 2 + Alembic, PostgreSQL (Supabase, session pooler), httpx,
pydantic-settings, pytest, ruff. Email via Resend. Web app (Google sign-in via Firebase): FastAPI on Render.
Daily run: GitHub Actions. LLM via an LLMClient interface (Gemini default).

## Rules
- Every job source is a class in src/jobhunter/sources/ implementing SourceAdapter and returning RawJob. Never put source-specific code in pipeline.py.
- One failing source or LLM call must never stop a run. Catch, log to the runs table, continue.
- No network in tests. Use saved fixtures in tests/fixtures/.
- User settings (titles, experience range, cities, Gmail) live in the database, per user, and are edited in the web UI. Never hard-code them or read them from files.
- Users sign in with Google (Firebase ID tokens checked in web/firebase_auth.py). Who may sign in and who is admin come from OWNER_EMAIL, ALLOWED_EMAILS and ADMIN_EMAILS (accounts.Access); never hard-code email addresses. Every query on a per-user table (search_profiles, user_settings, mail_accounts, job_scores, job_status, job_owners) filters by user_id; jobs are shared, except private ones from a user's Gmail (pipeline.visible_to).
- Never commit secrets. Read them from env via settings.py. Never print them.
- Never automate a logged-in LinkedIn or Naukri account, and never scrape their sites
  (no browser impersonation). Their jobs come only from alert emails read from the user's
  own Gmail over IMAP with an app password.
- The Chrome extension (extension/) may read the one job page the user has open in their
  own browser. It never clicks, scrolls, navigates, searches, polls faster than once a
  second, or sends anything to LinkedIn, Naukri or Indeed. Only its service worker holds
  the token and talks to the JobHunter API (/api/v1, bearer token, hashed in the database).
- Extension tests: `npm test` in extension/ (Node 22, linkedom). No network in tests.
- The Gmail app password is stored only encrypted (crypto.py, SETTINGS_KEY) and is never
  shown on a page, logged, or included in an error message.
- Respect rate limits: Adzuna max 250 calls/day, Resend max 100 emails/day. Count calls in the database.
- Run `ruff check` and `pytest` before saying a task is done.
- Keep functions small; add type hints; dedupe via jobs.dedupe_key.

## Commands
- python -m jobhunter migrate | run | test-email
- pytest -q
