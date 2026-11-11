# JobHunter

A scheduled Python service that finds India-based jobs for your target titles once a day, filters them against your experience range and locations, and emails you a short digest of new matches. It runs in the cloud every morning at about 7:45 AM IST, so your laptop can stay off, and it runs entirely on free tiers.

- **Sources:** Adzuna India, Amazon jobs, the careers sites (Greenhouse, Lever, Ashby, Workday, SmartRecruiters) of a 125-company watchlist of Indian tech companies and MNCs with India offices, and the Remotive and Remote OK remote feeds. Every source is a plug-in adapter; more are on the [roadmap](#roadmap).
- **Settings in the app, not in files:** job titles, experience range, cities, work modes and the digest address are edited on a small password-protected web page and stored in PostgreSQL.
- **Resilient:** a failing source never stops a run. You get a failure alert email, and a weekly "still alive" email, so silence never hides a broken pipeline.
- **Cost:** ₹0 a month (GitHub Actions, Supabase, Resend, Render and Adzuna free tiers).

## How it works

```
GitHub Actions (daily 7:43 IST, backups 9:13 and 11:43 IST)
   └─ python -m jobhunter run --once-per-day
        1. read search profiles + settings ........ PostgreSQL (Supabase)
        2. fetch from each enabled source ......... Adzuna India API, Greenhouse /
                                                     Lever / Ashby boards, Remotive, Remote OK
        3. normalize: city, work mode, country, dedupe key
        4. save new jobs (duplicates collapse on dedupe_key)
        5. hard filters on every recent, unsent job
        6. pick the best unsent matches ........... digest
        7. email the digest, mark jobs as sent .... Resend
        8. on problems: alert email; on Sundays: weekly heartbeat

Settings page (FastAPI on Render) ──writes──▶ the same PostgreSQL tables
```

### Filters

Each job is kept only if it passes all of these. A dropped job stores a readable reason, for example `asks for 3+ years (your max is 1)`.

| Filter | Rule |
|---|---|
| Title | Every word of one of your titles appears in the job title. "Developer", "Engineer" and "Programmer" count as the same word; "Back-end"/"backend" and "SDE"/"Software Engineer" match. |
| Exclude words | Whole-word match in title or description, for example `unpaid`. |
| Location | India-based, or remote. Remote jobs saying "US only", "EU residents" and similar are dropped. Remote jobs that never mention India are kept with a small score penalty. |
| Work mode and city | From your profile. Remote jobs skip the city check, and a job whose location names no city ("India") passes. Delhi covers Delhi NCR (Gurugram, Noida, Greater Noida, Faridabad, Ghaziabad); Mumbai covers Navi Mumbai and Thane. |
| Experience | Senior/Staff/Lead/Principal/Manager titles are dropped when your maximum is under 5 years. Level titles count as years: II/2/Intermediate ≈ 2, III/3 ≈ 4, IV/4 and above ≈ 6, and are dropped when above your maximum. Text such as "2+ years" or "1–3 yrs exp" is compared with your range. Jobs that state no experience are kept with a small penalty. |
| Recency | Jobs older than your maximum age (default 14 days) are dropped. |

Until embedding and LLM scoring arrive (Phase 4), the score is 1.0 minus 0.1 for unstated experience and minus 0.1 for remote roles that never mention India.

### Sources

| Source | How it is read | Notes |
|---|---|---|
| Adzuna India | Official API with a free key | Descriptions are cut to about 500 characters |
| Greenhouse, Lever, Ashby | Public careers-page APIs, no key | One request per company on the watchlist. |
| SmartRecruiters | Official public Posting API, no key | India postings only (`country=in`), for example Swiggy, Bosch, ServiceNow, Freshworks. |
| Workday | The public JSON each Workday careers site loads, no login | Uses the site's own India filter so jobs come newest first, stops at jobs older than the age limit (at most 5 pages), for example Nvidia, Adobe, Salesforce, Intel, Cisco, Visa, Citi, Mastercard. |
| Amazon | amazon.jobs search JSON, newest first | Up to 300 newest India jobs per run. |
| Remotive, Remote OK | Public feeds, one call per run | Only roles open to India or worldwide. Links point back to the source, as their terms require. |

For the careers sites, a job is kept only if the title matches, it is in India or remote-open-to-India (plain "Remote" on a global company's board is skipped because it is almost always US-only), and the title is not too senior for your experience range. The description is fetched only after those checks, to keep requests few. One broken site is skipped; a source fails only if most of its sites fail.

The **company watchlist** (125 companies, from [data/company_watchlist.yaml](data/company_watchlist.yaml)) is edited on the settings page, one `Name | ats | slug` per line. The slug is the company's name in its careers URL: `jobs.lever.co/cred` → `cred`; for Workday `tenant.wdN/Site`, for example `nvidia.wd5/NVIDIAExternalCareerSite`. When a new version of the default list ships, `migrate` adds only the new companies; your edits and removals are kept. Companies with their own portals and no public feed (Google, Microsoft, Flipkart, TCS, Infosys and others) are reached through Adzuna and job-alert emails.

### Rate limits

Every call to a rate-limited API is counted in the `api_usage` table before it is made, and a run stops calling when a budget is used up.

| Service | Budget in JobHunter | Free limit |
|---|---|---|
| Adzuna | 40 per run, 250 per day, 2,500 per month | 25/min, 250/day, 2,500/month |
| Resend | 5 per run, 100 per day, 3,000 per month | 100/day, 3,000/month |

## Tech stack

Python 3.12 · SQLAlchemy 2 + Alembic · PostgreSQL (Supabase) · httpx · pydantic-settings · FastAPI + Jinja2 (settings page) · Resend (email) · GitHub Actions (schedule) · Render (web hosting) · pytest · ruff

## Project layout

```
src/jobhunter/
  __main__.py          CLI: migrate, run, set-titles, set-email, test-email, heartbeat
  settings.py          secrets from environment / .env
  db.py                SQLAlchemy models and engine (with connect retry)
  models.py            RawJob: the one format every source returns
  normalize.py         city, country, work mode and dedupe key
  pipeline.py          fetch → normalize → dedupe → filter → digest
  usage.py             call budgets for rate-limited APIs
  user_settings.py     load and seed settings stored in the database
  sources/             one adapter per source (adzuna, greenhouse, lever, ashby, workday,
                       smartrecruiters, amazon, remotive, remoteok), boards.py (shared
                       careers-site logic), watchlist.py,
                       registry.py (which sources run), catalog.py, base.py
  matching/filters.py  hard filters
  notify/              digest email (Resend), failure alerts, weekly heartbeat
  web/                 settings page: app, form validation, templates
alembic/               database migrations
tests/                 pytest suite; fixtures/ holds saved API responses
.github/workflows/     daily scheduled run
render.yaml            Render blueprint for the settings page
config.yaml            first-run defaults only; real settings live in the database
data/                  default company watchlist, seeded into the database once
```

## Getting started

### 1. Accounts and keys

| Key | Where to get it |
|---|---|
| `DATABASE_URL` | Supabase → Project Settings → Database → **Session pooler** string (port 5432). The direct connection is IPv6-only and unreachable from GitHub Actions; the transaction pooler (6543) breaks psycopg and migrations. |
| `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` | [developer.adzuna.com](https://developer.adzuna.com) |
| `RESEND_API_KEY` | [resend.com](https://resend.com). Without your own domain, Resend only delivers to the address you signed up with. |
| `WEB_PASSWORD` | Any long password, for the settings page. |
| `SESSION_SECRET` | 32+ random characters: `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `ALERT_TO` (optional) | Where failure alerts go, so they still arrive if the database is down. |
| `TEST_DATABASE_URL` (optional) | A **local** Postgres database for the database tests. Never the hosted one. |

### 2. Install and configure

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,web]"
```

Create a `.env` file in the project root with the keys above (one `NAME=value` per line). `.env` is in `.gitignore`; never commit it. If a password contains `@ # / : ? %`, URL-encode it in connection strings (`@` → `%40`, `#` → `%23`).

A `DATABASE_URL` set in your terminal overrides `.env`. If the app says it needs PostgreSQL, clear the terminal variable with `Remove-Item Env:DATABASE_URL`.

### 3. Create the tables and run

```powershell
.\.venv\Scripts\python.exe -m jobhunter migrate              # tables + default settings
.\.venv\Scripts\python.exe -m jobhunter set-email you@example.com
.\.venv\Scripts\python.exe -m jobhunter set-titles "Backend Engineer" "Python Developer"
.\.venv\Scripts\python.exe -m jobhunter test-email           # one sample job
.\.venv\Scripts\python.exe -m jobhunter run                  # fetch, filter, email
```

Running `run` twice sends the digest once; the second run reports `no new jobs, no email sent`.

### Commands

| Command | What it does |
|---|---|
| `migrate` | Applies Alembic migrations and seeds missing default settings. Safe to repeat. |
| `run` | One full cycle. Exits with code 1 and emails an alert if anything went wrong. With `--once-per-day` it does nothing if a run already succeeded today (IST). |
| `set-titles TITLE...` | Sets the job titles (the settings page does the same). |
| `set-email ADDRESS...` | Sets the digest recipients. |
| `test-email` | Sends a one-job sample digest. |
| `heartbeat` | Sends the weekly summary now. |

## Settings page

Run it locally:

```powershell
$env:WEB_HTTPS_ONLY = "false"   # local http only; keep secure cookies online
.\.venv\Scripts\python.exe -m uvicorn jobhunter.web.app:create_app --factory --port 8000
```

Then open http://127.0.0.1:8000 and sign in with `WEB_PASSWORD`. Titles, cities and exclude words can be separated by commas or new lines. Saved changes apply on the next run.

Security: a single password compared in constant time, a 1-second delay after a wrong guess, a signed HTTPS-only session cookie, a CSRF token on every form, escaped output, and headers that block framing. The app refuses to start without `WEB_PASSWORD` and `SESSION_SECRET`.

## Deployment

**Daily run (GitHub Actions).** Add these repository secrets under *Settings → Secrets and variables → Actions*: `DATABASE_URL`, `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`, `RESEND_API_KEY` and optionally `ALERT_TO`. The workflow in [.github/workflows/run.yml](.github/workflows/run.yml) is scheduled at 7:43 AM IST, with backups at 9:13 and 11:43 AM IST. GitHub can delay scheduled runs, and at busy times (especially the top of the hour) it can drop them, so the main time is off the hour and every scheduled run uses `--once-per-day`: a backup only works if no run has succeeded yet that day, so you never get two digests. The **Run workflow** button on the Actions tab always runs.

**Settings page (Render).** In the Render dashboard choose **New → Blueprint**, pick this repository, and enter `DATABASE_URL` and `WEB_PASSWORD` when asked; Render generates `SESSION_SECRET`. [render.yaml](render.yaml) deploys a free web service in Singapore and redeploys on every push to `main`. Free services sleep when idle, so the first visit after a break takes up to a minute. The daily run does not depend on the page.

## Testing

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pytest -q
```

Tests make no network calls: API responses are replayed from `tests/fixtures/`, and HTTP is mocked. Database tests (pipeline and settings store) run in a throwaway schema on `TEST_DATABASE_URL` and are skipped when it is not set.

## Safety rules

- Secrets live only in `.env`, GitHub Actions secrets and Render. Error messages and alert emails never contain API keys, URLs with keys, or database passwords.
- A logged-in LinkedIn account is never automated. LinkedIn jobs will come only from LinkedIn's own alert emails (Phase 3).
- Job text sent to an LLM (Phase 4) will never include personal details.

## Roadmap

| Phase | Status | Scope |
|---|---|---|
| 0 | Done | Project skeleton, settings, database migrations |
| 1 | Done | Adzuna adapter, normalizer, filters, digest email, settings page on Render |
| 2 | In progress | Daily GitHub Actions run, failure alerts, weekly heartbeat (waiting on the first scheduled run) |
| 3 | In progress | Done: careers sites (Greenhouse, Lever, Ashby, Workday, SmartRecruiters) from an editable 125-company watchlist; Amazon; Remotive and Remote OK. Next: LinkedIn, Naukri and Indeed alert emails read from Gmail |
| — | Planned | Chrome companion extension: reads the job cards you open on LinkedIn, Naukri or Indeed (no automatic browsing), highlights matches using the same filters, and saves jobs to JobHunter |
| 4 | Planned | Embedding score from a profile, title and skill scores, optional LLM review with a no-LLM fallback |
| 5 | Planned | Applied / Not interested / Save links in the email; rejections lower similar jobs |
| 6 | Planned | Job list and status pages in the web app; optional resume and cover-letter drafts |
