# JobHunter

A scheduled Python service that finds India-based jobs for your target titles once a day, filters them against your experience range and locations, and emails you a short digest of new matches. It runs in the cloud every morning at about 7:45 AM IST, so your laptop can stay off, and it runs entirely on free tiers.

- **Sources:** Adzuna India, Amazon jobs, the careers sites (Greenhouse, Lever, Ashby, Workday, SmartRecruiters) of a 125-company watchlist of Indian tech companies and MNCs with India offices, and the Remotive and Remote OK remote feeds. Every source is a plug-in adapter; more are on the [roadmap](#roadmap).
- **Accounts:** anyone signs in with Google (Firebase) and gets their own job titles, experience range, cities, work modes, Gmail job alerts and a **My jobs** page. Jobs are fetched once a day for everyone, then filtered for each person. Settings live in PostgreSQL, not in files.
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

### Fit score

Jobs that pass the filters are ranked by how well they fit your resume. Upload it on **Settings → My profile** (PDF up to 2 MB and 10 pages, or paste the text). Only cleaned text is kept: email addresses, phone numbers and links are removed and the PDF itself is never stored. You can edit the text, and the skills found in it are shown as tags.

| Part | Rule |
|---|---|
| Skills (65%) | Share of the job's skills that are in your profile, from a built-in list of about 300 skills and their other names (JS = JavaScript, Postgres = PostgreSQL, k8s = Kubernetes). Skills in the job title count twice. A job naming no known skill counts as half. |
| Title (35%) | How closely the job title matches your titles. |
| Penalties | Minus 0.1 for unstated experience, minus 0.1 for a remote role that never mentions India. |
| Level | **Strong** 0.75 and up, **Good** 0.55 and up, otherwise **Weak**. |

Each match shows its level and a reason such as *Matches Python, Django, SQL · Missing AWS* in My jobs, the daily email and the Chrome bubble. On Settings you choose what the daily email includes: strong fits only, good and strong (default), or everything that passed. My jobs always lists every match, best first. Without a profile, the score is 1.0 minus the penalties, as before.

**Gemini review (optional, off by default).** Tick *Let Gemini review my top 10 jobs each day* and the daily run sends your profile text (without contact details or its first line, usually your name) and your 10 best new jobs to Google's Gemini in one call. Gemini rates each 1–5 with a short reason; the final score becomes half the local score and half Gemini's rating, and its reason replaces the local one. If Gemini fails or `GEMINI_API_KEY` is not set, the local scores stay and the run carries on. After the update, run `python -m jobhunter rescore` once to find skills in jobs saved earlier.

### Email actions and "Not interested"

Each job in the daily email has **Save**, **Applied** and **Not interested** links that work with one tap, without signing in. A link carries a signed code for that one user, job and action (key derived from `SETTINGS_KEY`), expires after 30 days, and never signs anyone in. Opening it changes nothing by itself: the page then records the action in your browser, so email link scanners cannot trigger it. On My jobs the same **Not interested** button is next to Save and Applied.

After **Not interested** you can pick an optional reason:

| Reason | Effect |
|---|---|
| Don't like this company | Jobs from that company are hidden (Pvt, Ltd and similar endings ignored). |
| Wrong skills | The job's skills you don't have are lowered: −0.1 each in later jobs, at most −0.3. |
| Wrong location | Non-remote jobs in that city are hidden. |
| Not my kind of role | Jobs with the same title rank 0.2 lower. |

Lowered jobs say why ("Lowered: you avoid AWS"). Everything is listed under **Settings → Hidden and lowered**, where **Remove** undoes it. Links point to `PUBLIC_URL` (default `https://jobhunter-settings.onrender.com`).

### Digest from your own Gmail

Without a sending domain of your own, Resend can only email the owner. Anyone else can still get the daily email: connect Gmail on **Settings → Gmail job alerts** (an app password, which needs Google 2-Step Verification), then tick **Send my daily digest from this Gmail**. The digest then goes from that Gmail to your digest address, using the same app password; nothing else is needed. It is off by default, and the owner can use it too. If Gmail refuses the password, the run reports "Gmail refused the app password" and carries on with everyone else.

### Cover letter and resume tips

Every job on My jobs has a **Draft** link. On its page, **Write draft** asks Gemini for a 200–250 word cover letter and 3–5 resume tips for that job, from your profile text (without contact details or its first line, so the letter ends with *[Your name]* for you to fill in). Gemini is told to use only facts from your profile and to treat the job ad as untrusted text; its answer is checked and shown as plain text. Edit the letter and **Save edits**, or **Copy** it. The latest draft per job is kept, with your edits; **Redo** writes a fresh one after asking first. Each person can make 10 drafts a day (India time); a failed attempt counts too, and a failure never touches the saved draft. Nothing is sent unless you click Write draft or Redo. Needs `GEMINI_API_KEY` on Render.

### Sources

| Source | How it is read | Notes |
|---|---|---|
| Adzuna India | Official API with a free key | Descriptions are cut to about 500 characters |
| Greenhouse, Lever, Ashby | Public careers-page APIs, no key | One request per company on the watchlist. |
| SmartRecruiters | Official public Posting API, no key | India postings only (`country=in`), for example Swiggy, Bosch, ServiceNow, Freshworks. |
| Workday | The public JSON each Workday careers site loads, no login | Uses the site's own India filter so jobs come newest first, stops at jobs older than the age limit (at most 5 pages), for example Nvidia, Adobe, Salesforce, Intel, Cisco, Visa, Citi, Mastercard. |
| Amazon | amazon.jobs search JSON, newest first | Up to 300 newest India jobs per run. |
| Remotive, Remote OK | Public feeds, one call per run | Only roles open to India or worldwide. Links point back to the source, as their terms require. |
| LinkedIn and Naukri job alerts | Your own Gmail over IMAP, with a Google app password | Reads alert emails from the configured senders, parses the jobs from their links, and labels each email `JobHunter` so it is read once. JobHunter never logs in to LinkedIn or Naukri. |

**Setting up job alerts:** one alert per site is enough. On LinkedIn, search with OR, for example `"software engineer" OR "backend" OR "python" OR "AI engineer" OR "data engineer"`, set the location to India, and switch on the alert. On Naukri, enter the roles comma-separated in one search and create a job alert. Then, on the settings page, add the Gmail address the alerts go to and a Google **app password** (needs 2-Step Verification), and click **Test connection**. The app password is stored encrypted with `SETTINGS_KEY`, which must be the same in `.env`, GitHub Actions secrets and Render.

For the careers sites, a job is kept only if the title matches, it is in India or remote-open-to-India (plain "Remote" on a global company's board is skipped because it is almost always US-only), and the title is not too senior for your experience range. The description is fetched only after those checks, to keep requests few. One broken site is skipped; a source fails only if most of its sites fail.

The **company watchlist** (125 companies, from [data/company_watchlist.yaml](data/company_watchlist.yaml)) is edited on the settings page, one `Name | ats | slug` per line. The slug is the company's name in its careers URL: `jobs.lever.co/cred` → `cred`; for Workday `tenant.wdN/Site`, for example `nvidia.wd5/NVIDIAExternalCareerSite`. When a new version of the default list ships, `migrate` adds only the new companies; your edits and removals are kept. Companies with their own portals and no public feed (Google, Microsoft, Flipkart, TCS, Infosys and others) are reached through Adzuna and job-alert emails.

### Rate limits

Every call to a rate-limited API is counted in the `api_usage` table before it is made, and a run stops calling when a budget is used up.

| Service | Budget in JobHunter | Free limit |
|---|---|---|
| Adzuna | 40 per run, 250 per day, 2,500 per month | 25/min, 250/day, 2,500/month |
| Resend | 5 per run, 100 per day, 3,000 per month | 100/day, 3,000/month |
| Gmail (sending) | 50 per day, 1,500 per month | about 500/day per account |

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
| `FIREBASE_API_KEY`, `FIREBASE_AUTH_DOMAIN`, `FIREBASE_PROJECT_ID`, `FIREBASE_APP_ID` | Firebase console → Project settings → your web app → **Config**. Turn on **Authentication → Google**, and add your Render domain under **Authentication → Settings → Authorized domains**. These four values are public; no Firebase private key is used. |
| `OWNER_EMAIL` | Your Google sign-in email. That account becomes the owner: it gets the data from before accounts existed and the **Admin** page. |
| `ALLOWED_EMAILS` | Optional. Comma-separated Google emails. When set, JobHunter is invite-only: only these accounts, the admins and the owner can sign in; anyone else sees "invite-only". Removing an address ends that person's sessions and extension connection. Empty = anyone with Google. |
| `ADMIN_EMAILS` | Optional. Comma-separated Google emails that also get the **Admin** page (users, blocking, sources, watchlist). Nobody can block the owner. |
| `SESSION_SECRET` | 32+ random characters: `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `ALERT_TO` (optional) | Where failure alerts go, so they still arrive if the database is down. |
| `GEMINI_API_KEY` | Optional, for the Gemini review: [aistudio.google.com](https://aistudio.google.com) → *Get API key* (free tier). Needed in `.env` and GitHub Actions for the daily review, and on Render for cover letter drafts. `GEMINI_MODEL` defaults to `gemini-3.1-flash-lite` (Google no longer gives new keys `gemini-2.5-flash`). |
| `SETTINGS_KEY` | Encrypts the Gmail app password: `python -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"`. Use the same value locally, in GitHub Actions and on Render. |
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
| `make-owner [--apply]` | If you first signed in before `OWNER_EMAIL` was set, makes that account the owner: keeps the placeholder's emailed marks, private jobs and missing settings, then deletes the placeholder. Shows what would change unless `--apply` is given. |

## Web app

Pages: **My jobs** (your matches, best first, with Save / Applied / Not interested; tabs with counts; *Strong fit* and *Good fit & up* filters on Matches; Saved and Applied show when you saved or applied, e.g. "Applied 3 days ago"), **Settings** (titles, experience, cities, work modes, exclude words, digest, Gmail job alerts), and **Admin** for the owner (users, block/unblock, job sources and the company watchlist, which everyone shares).

Run it locally:

```powershell
$env:WEB_HTTPS_ONLY = "false"   # local http only; keep secure cookies online
.\.venv\Scripts\python.exe -m uvicorn jobhunter.web.app:create_app --factory --port 8000
```

Then open http://localhost:8000 (`localhost` is an authorised Firebase domain by default) and click **Continue with Google**. Titles, cities and exclude words can be separated by commas or new lines. Saved changes apply on the next run.

**Accounts and limits.** Sign-up is open to any Google account with a verified email (or only to `ALLOWED_EMAILS` when that is set), up to 50 users (later sign-ins see "JobHunter is full"; the owner always gets in), with at most 15 job titles each. The first sign-in with `OWNER_EMAIL` claims the owner account. To hand ownership to another account later, set `users.is_owner` in the database by hand.

**Who gets email.** Resend's free plan without your own domain only delivers to your own address, so only the owner gets the daily email. Everyone else sees their matches on My jobs. Once you verify a domain in Resend, set `digest_from` in `app_settings` to an address on it (for example `JobHunter <jobs@yourdomain.in>`) and every user with a digest address gets an email too; no code change.

**Security.** The server checks each Google sign-in token itself (RS256 signature against Google's public certificates, audience, issuer, expiry, verified email), then keeps a signed, HTTPS-only session cookie for 30 days. Every form has a CSRF token, every per-user query is filtered by user id, jobs from someone's Gmail are visible only to them, output is escaped, and headers block framing. The app refuses to start without `SESSION_SECRET`.

## Deployment

**Daily run (GitHub Actions).** Add these repository secrets under *Settings → Secrets and variables → Actions*: `DATABASE_URL`, `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`, `RESEND_API_KEY`, `SETTINGS_KEY` and optionally `ALERT_TO` and `GEMINI_API_KEY`. The workflow in [.github/workflows/run.yml](.github/workflows/run.yml) is scheduled at 7:43 AM IST, with backups at 9:13 and 11:43 AM IST. GitHub can delay scheduled runs, and at busy times (especially the top of the hour) it can drop them, so the main time is off the hour and every scheduled run uses `--once-per-day`: a backup only works if no run has succeeded yet that day, so you never get two digests. The **Run workflow** button on the Actions tab always runs.

**Web app (Render).** In the Render dashboard choose **New → Blueprint**, pick this repository, and enter `DATABASE_URL`, `SETTINGS_KEY`, `OWNER_EMAIL` and the four `FIREBASE_*` values when asked; Render generates `SESSION_SECRET`. [render.yaml](render.yaml) deploys a free web service in Singapore, applies database migrations on every build, and redeploys on every push to `main`. Free services sleep when idle, so the first visit after a break takes up to a minute. The daily run does not depend on the page.

## Chrome extension (the JobHunter bubble)

A small floating bubble on job pages: LinkedIn (`/jobs/` pages), Naukri, Indeed, and company careers pages on Greenhouse, Lever, Ashby, Workday and SmartRecruiters. When you open a job it shows, with a green or grey ring, whether the job matches your filters (and why not); **Save to JobHunter** and **Mark applied** put it on My jobs; the panel lists your latest matches; a dot marks jobs you have already saved, applied to, or received by email.

It only reads the job page you have open, in your own browser. It never clicks, scrolls, opens pages or searches for you, and never sends anything to LinkedIn, Naukri or Indeed; it talks only to your JobHunter server. It needs only the `storage` permission and network access to the JobHunter server.

**Install:** Chrome → `chrome://extensions` → **Developer mode** on → **Load unpacked** → choose the `extension` folder. Then JobHunter → Settings → **Connect extension** → **Connect this browser**. Settings lists connected browsers with **Disconnect**. The toolbar popup switches automatic checking on or off and brings back the bubble on sites where you hid it. On LinkedIn, if you arrive at the jobs pages from the feed, reload the page once so the bubble appears.

## Testing

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pytest -q
```

Tests make no network calls: API responses are replayed from `tests/fixtures/`, and HTTP is mocked. Database tests (pipeline and settings store) run in a throwaway schema on `TEST_DATABASE_URL` and are skipped when it is not set.

## Safety rules

- Secrets live only in `.env`, GitHub Actions secrets and Render. Error messages and alert emails never contain API keys, URLs with keys, or database passwords.
- LinkedIn and Naukri accounts are never logged into or automated, and their sites are never scraped. Their jobs come only from their own alert emails in your Gmail.
- The Gmail app password is stored encrypted and is never shown, logged or put in an error message.
- Only cleaned resume text is stored (no email, phone or links); the uploaded PDF is never stored or logged. Gemini sees it, without its first line, only for users who turn the review on or click Write draft for a job.

## Roadmap

| Phase | Status | Scope |
|---|---|---|
| 0 | Done | Project skeleton, settings, database migrations |
| 1 | Done | Adzuna adapter, normalizer, filters, digest email, settings page on Render |
| 2 | In progress | Daily GitHub Actions run, failure alerts, weekly heartbeat (waiting on the first scheduled run) |
| 3 | Done | Careers sites (Greenhouse, Lever, Ashby, Workday, SmartRecruiters) from an editable 125-company watchlist; Amazon; Remotive and Remote OK; Gmail alert reader with LinkedIn and Naukri parsers checked against real alert emails |
| A | Done | Google sign-in for many users, per-user settings and Gmail, My jobs page, owner Admin page, new marigold look and logo |
| B | Done | Chrome bubble extension: on the job you open on LinkedIn, Naukri, Indeed or a careers site (no automatic browsing), shows whether it matches your filters, saves it to My jobs, lists your latest matches and marks jobs you have seen |
| 4 | Done | Fit score from your resume (skills and title), Strong/Good/Weak levels with reasons, digest fit choice, optional Gemini review of your top 10 a day |
| 5 | Done | One-click Save / Applied / Not interested links in the email; optional reasons hide or lower similar jobs, undoable on Settings |
| 6 | Done | Cover letter (editable, 200–250 words) and resume tips per job from Gemini, on request, 10 a day |
