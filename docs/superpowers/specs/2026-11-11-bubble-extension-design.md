# Sub-project B: JobHunter bubble (Chrome extension)

Status: draft for review, 2026-11-11. Builds on sub-project A (accounts), which must ship
first.

## Goal

A small floating JobHunter bubble on job pages. When you open a job, the bubble tells
you whether it matches your JobHunter filters, lets you save it to your account, shows
your latest matches, and marks jobs you have already seen.

### What the user asked for
- A small floating bubble on LinkedIn and other job platforms, with the logo from A.
- All four features: match check on the open job, Save to JobHunter, today's matches
  list, already-seen badge.
- Sites: LinkedIn, Naukri, Indeed, company career pages (Greenhouse, Lever, Ashby,
  Workday, SmartRecruiters).

### Hard rule (from CLAUDE.md and the earlier decision)
The extension is **passive**. It reads only the job page you have open, in your own
browser. It never clicks, scrolls, opens pages, refreshes, searches or runs in the
background on these sites, and never sends anything to LinkedIn, Naukri or Indeed.

## Non-goals
- Applying to jobs, auto-filling forms, or any action on the job site.
- Reading job lists or search results (only the one job you have open).
- Firefox/Safari. Edge works with the same build (Chromium), untested at first.
- Chrome Web Store publishing (needs the owner's $5 developer account). First release
  is "Load unpacked"; the store listing is a later step with no code changes.

## Pieces

```
Job page (your browser)                         JobHunter server (Render)
┌─────────────────────────────┐   message   ┌──────────────┐  HTTPS + token  ┌──────────────┐
│ content script              │ ──────────▶ │ service      │ ──────────────▶ │ /api/v1/...  │
│ • site reader → JobInfo     │ ◀────────── │ worker       │ ◀────────────── │ (FastAPI)    │
│ • bubble UI (shadow DOM)    │             │ • token      │                 │ per-user     │
└─────────────────────────────┘             │ • API calls  │                 │ filters, DB  │
                                            └──────────────┘                 └──────────────┘
```

- **Manifest V3** extension in `extension/` (plain JavaScript modules, no build step).
- **Content script** runs only on the listed job sites. It reads the open job and draws
  the bubble.
- **Service worker** holds the token and makes all calls to the JobHunter server, so job
  sites never see the token and no CORS rules are needed.
- **Server API** is new routes in the existing FastAPI app.

Permissions: `storage`; host permissions only for the job sites listed below and
`https://jobhunter-settings.onrender.com/*`. No `tabs`, `history`, `cookies`,
`webRequest` or `<all_urls>`.

## Reading the open job (site readers)

Each reader is a function `read(document, location) -> JobInfo | null`, where
`JobInfo = {url, title, company, location, description?}`.

| Site | Where the job is | How it is read |
|---|---|---|
| Company career pages (Greenhouse, Lever, Ashby, Workday, SmartRecruiters) | job page | the page's `JobPosting` structured data (`<script type="application/ld+json">`), else the page title/heading |
| Indeed (`in.indeed.com`) | job page or the right-hand job pane | `JobPosting` data when present, else the job pane's title/company/location elements |
| LinkedIn (`linkedin.com/jobs/...`) | job page or right-hand pane (`currentJobId=`) | the visible job's title, company and location elements; job URL rebuilt as `https://www.linkedin.com/jobs/view/<id>/` |
| Naukri (`naukri.com/job-listings-...`) | job page | title, company, location and experience elements; URL without tracking |

The job sites change their pages often, so readers are small, one file per site, each
with tests on a saved copy of a real page (personal details removed). When a reader
cannot find a job, the bubble shows "Open a job to check it" instead of guessing.

Single-page sites (LinkedIn, Indeed) change the job without loading a new page; the
content script notices URL changes and re-reads (no polling faster than once a second,
and only while the tab is visible).

## Bubble

- 48 px round button with the logo, bottom-right, draggable; its position is
  remembered per site. A hide option for the current site ("Hide on linkedin.com"),
  undone from the extension's popup.
- Ring colour: **green** = matches your filters; **grey** = does not match; **marigold
  outline** = checking or no job open. A small dot badge = already seen (saved, applied,
  or already in your digest/My jobs).
- Click opens a panel (320 px wide):
  - **This job**: title and company; "Matches your filters" or "Not a match:
    <reasons>" (e.g. "needs 5+ years", "Bengaluru is not in your cities"); seen status.
  - **Save to JobHunter** button (becomes "Saved ✓"). Also "Mark applied".
  - **Your latest matches**: top 5 from My jobs, each opening in a new tab.
  - Link "Open My jobs".
- Built inside a **shadow DOM** so the site's CSS cannot break it and it cannot break
  the site. Uses the palette from A.
- Not signed in: the panel says "Connect JobHunter" with a button that opens the
  connect page.
- Server asleep (Render free tier): "Waking up JobHunter, this can take up to a
  minute…", then retries once.

Match check happens automatically when a job opens (needed for the ring colour). It
sends only that job's title, company, location, URL and (if read) description to your
own JobHunter server. A setting in the extension popup turns automatic checking off
(then checks happen only when you click the bubble).

## Connecting the extension to your account

1. Settings page (A) gets an **Extension** card with **Connect extension**.
2. That opens `/extension/connect` (signed-in page). The extension's content script runs
   on that one page; when you click **Connect** there, the page creates a new
   extension token and the content script passes it to the service worker, which stores
   it in `chrome.storage.local`.
3. Tokens: 32 random bytes, shown to nobody, stored in the database only as a SHA-256
   hash, with a label ("Chrome on Windows", from the browser), created and last-used
   times. The Extension card lists connected browsers with **Disconnect**. Blocking a
   user (A) disables their tokens.

## Server API (new, in the FastAPI app)

All under `/api/v1`, `Authorization: Bearer <token>`, JSON in and out, never uses the
session cookie, so CSRF does not apply. Errors: 401 bad/expired token, 422 bad input,
429 too many requests.

| Route | In | Out |
|---|---|---|
| `POST /api/v1/check` | JobInfo | `{verdict: "match"\|"no_match", reasons: [..], seen: {status, emailed}}` |
| `POST /api/v1/jobs` | JobInfo + `status` (`saved`\|`applied`) | `{job_id, status}`; stores the job as private to the user (like Gmail jobs) and sets the status |
| `GET /api/v1/matches?limit=5` | — | `[{title, company, location, url, posted_at}]` (same list as My jobs) |
| `GET /api/v1/me` | — | `{email, name}` (for the popup) |

- The check uses the same `apply_filters` as the daily run with the user's own
  profiles, after the same `normalize_job` (so "seen" is found by `dedupe_key`).
- Input limits: title/company/location ≤ 300 chars, description ≤ 20,000, URL must be
  `https` on one of the supported sites.
- Rate limit: 300 requests per token per hour (in memory per server instance; fine for
  one free instance).

## Logo files
From A's SVG: PNG 16/32/48/128 for the extension icons, generated once and committed
(script in `extension/tools/`, run by hand).

## Testing
- Server: API tests with fake tokens (check verdicts and reasons; save makes a private
  job; matches only the user's; another user's token cannot see or change it; 401/422/429).
- Token lifecycle: create, list, disconnect, blocked user.
- Readers: one test per site with a saved, cleaned page in `extension/test/fixtures/`,
  run with Node's built-in test runner and `linkedom` (small DOM library, dev-only).
  Needs Node.js 20+ on the user's computer for running these tests only.
- Bubble: manual checklist on each site (open job → ring colour; save; matches list;
  seen dot; hide on site; not connected; server asleep).

## Rollout
1. A is live.
2. Push B; Render deploys the API.
3. User loads `extension/` with Chrome → Extensions → Developer mode → Load unpacked.
4. Settings → Extension → Connect.
5. Later (optional): Web Store listing.
