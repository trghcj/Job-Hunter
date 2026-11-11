# Sub-project A: Accounts (Google sign-in) and new look

Status: draft for review, 2026-11-11.
Next after this: sub-project B, the floating-bubble Chrome extension (separate spec).

## Goal

Anyone can sign in to JobHunter with Google and get their own job search: their own
titles, cities, experience, work modes, Gmail job alerts and list of matches. The
shared web password goes away. The app gets a new non-blue look and a logo.

### What the user asked for
- Sign-up open to anyone with a Google account; Firebase Authentication for Google sign-in
  (project `job-hunter-8e5a6`, Google provider on, `jobhunter-settings.onrender.com`
  authorised).
- Each person separate (own settings, Gmail, matches).
- No email domain yet: only the owner gets the daily email; everyone else sees matches in
  the app (and later in the extension bubble).
- New UI that is not blue-ish; a logo designed by Claude, to be reviewed.

### Assumptions (correct me)
- The daily run stays one GitHub Actions job a day for everyone.
- Jobs are fetched once for all users and stored once (approach 1, agreed); filtering,
  matches, saved/seen state and Gmail are per user.
- The owner's current settings and history move to the owner's Google account.
- Free-tier safety caps: at most 50 users, at most 15 job titles per user.

## Non-goals (for A)
- The Chrome extension and its API (sub-project B).
- Email digests to users other than the owner (switches on later when a domain is set
  up; see "Email").
- Per-user company watchlists or per-user source choices: these stay one shared list,
  edited by the owner, because fetching is shared.
- LLM scoring (Phase 4).

## Users and roles

| Role | Who | Can |
|---|---|---|
| Owner | The Google account whose email equals `OWNER_EMAIL` (env) | Everything a user can, plus the Admin page: see users, block/unblock, edit sources and the company watchlist |
| User | Anyone else who signs in with Google (verified email) | Own settings, own Gmail, own matches |
| Blocked | A user the owner blocked | Sees "Your account is blocked"; nothing else |

When 50 users exist, new sign-ins see "JobHunter is full right now" (existing users are
not affected). The owner always gets in.

## Sign-in flow (Firebase)

1. `/login` shows the logo and a **Continue with Google** button. The page loads the
   Firebase JS SDK (modular, from `https://www.gstatic.com/firebasejs/<pinned version>/`)
   with the four public `FIREBASE_*` values from env.
2. `signInWithPopup(GoogleAuthProvider)` returns a Firebase **ID token**.
3. The page POSTs the token to `/auth/session` (with the CSRF token).
4. The server verifies the token itself; no Firebase private key is used:
   - RS256 signature against Google's public certificates
     (`https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com`),
     cached for the time their `Cache-Control` allows;
   - `aud == FIREBASE_PROJECT_ID`, `iss == https://securetoken.google.com/<project>`,
     not expired, `iat` not in the future, `sub` non-empty, `email_verified` true.
5. The server finds or creates the user by Firebase `sub` (uid), records email, name and
   last sign-in, then stores `user_id` in the signed session cookie (same
   SessionMiddleware as today, `SESSION_SECRET`).
6. The page then signs out of Firebase in the browser (the server session is what
   counts) and goes to **My jobs**.

Sign out clears the session. Sessions last 30 days.

Library: `PyJWT[crypto]` (new, in the `web` extra). The verifier is a small class with
an injectable certificate fetcher so tests need no network.

`WEB_PASSWORD` is removed. `SESSION_SECRET` stays.

### Owner claim
Migration creates an **owner placeholder** user (id 1, no Firebase uid) and gives it all
existing data. The first Google sign-in whose verified email equals `OWNER_EMAIL`
(case-insensitive) claims that placeholder. If `OWNER_EMAIL` is not set, nobody is owner
and the Admin page is unavailable.

## Data model (migration 0005)

New tables:
- `users`: `id`, `firebase_uid` (unique, null only for the unclaimed owner placeholder),
  `email`, `name`, `is_owner`, `blocked`, `created_at`, `last_login_at`.
- `user_settings`: (`user_id`, `key`) primary key, `value` JSONB. Holds per-user
  settings that today live in `app_settings`: `digest` (jobs per email), `digest_to`,
  `max_age_days`.
- `job_owners`: (`job_id`, `user_id`) primary key. Marks who may see a **private** job.

Changed tables:
- `jobs`: add `private` boolean, default false. Jobs from someone's Gmail alerts are
  private to that person. If the same job (same `dedupe_key`) later arrives from a public
  source, it becomes public. If a second user's Gmail brings the same private job, they
  are added to `job_owners`.
- `search_profiles`: add `user_id` (not null; existing rows go to the owner).
- `mail_accounts`: add `user_id` (unique, not null; existing row goes to the owner).
- `job_scores`: primary key becomes (`user_id`, `job_id`); existing rows go to the owner.
- `job_status`: primary key becomes (`user_id`, `job_id`); existing rows go to the owner.
  `status` values: `new`, `saved`, `applied`, `hidden`.

Stay global (shared): `jobs`, `runs`, `api_usage`, `llm_usage`, and in `app_settings`:
`sources`, `company_watchlist`, `watchlist_version`, `digest_from`, `last_heartbeat`.

A job is **visible** to a user when `private` is false, or a `job_owners` row links it to
them.

Every query in the web app and pipeline that reads per-user tables takes a `user_id`
and filters by it. Tests check that one user can never read or change another's data.

## Daily run

1. Load active users (not blocked, with at least one title).
2. **Shared fetch**: build one combined search from all users' titles (deduplicated,
   lower-cased). Adzuna's per-run budget (40 calls) is spent on the most common titles
   first. Board adapters pre-filter with the combined titles and the widest experience
   range among users, so no user's job is dropped before their own filter sees it.
   Remotive, RemoteOK, Amazon, Workday, etc. run once, as now.
3. **Per-user Gmail**: for each user with a saved Gmail, read their alerts (same
   read-only-then-label flow); store those jobs as private to that user.
4. **Per-user filter**: for each user, re-filter recent, visible jobs that the user has
   not been emailed and not hidden, using only that user's profiles; write
   `job_scores` rows for that user.
5. **Email**: the owner gets the digest exactly as today. Other users get no email unless
   email for all is turned on (see below).
6. Each step is logged in `runs`; one user's Gmail failure never stops others.

Run time grows slowly with users: fetching is shared; per-user filtering is in-memory
work over a few thousand jobs.

### Email
Resend's free sandbox sends only to the Resend account's own address. A setting
`digest_from` already exists. When the owner later sets it to an address on a domain
verified in Resend, digests go to every user who has a `digest_to`; until then only the
owner's digest is sent. No code change needed then.

## Pages

| Page | Who | What |
|---|---|---|
| `/login` | anyone | Logo, one line on what JobHunter does, Continue with Google |
| `/` **My jobs** (new, home) | user | Today's and recent matches, best first: title, company, city, work mode, source, posted date, Open link. Buttons: Save, Applied, Hide. Tabs: Matches / Saved / Applied. Empty state explains what to fill in on Settings. |
| `/settings` | user | Today's page split for one person: titles, experience, work modes, cities, exclude words, jobs per email, max age, digest email; Gmail job alerts card; recent runs (only rows about this user's Gmail plus the shared summary) |
| `/admin` | owner | Users table (email, joined, last sign-in, titles, Gmail on/off, block/unblock); "Where to look" sources and company watchlist (moved from settings); shared run history |
| `/blocked`, `/full` | — | Short explanation pages |

All forms keep CSRF tokens. All pages keep the existing security headers; the
Content-Security-Policy is widened only for what Firebase sign-in needs on `/login`
(`www.gstatic.com` scripts, `apis.google.com`, `*.firebaseapp.com` frames,
`identitytoolkit.googleapis.com` / `securetoken.googleapis.com` connections).

## New look

- **Palette**: warm and calm, no blue. Light: off-white paper `#FAF7F2`, ink
  `#1F1B16`, muted text `#6B6258`, borders `#E6DFD5`. Dark: `#16130F` background,
  `#201C17` cards, `#F2ECE3` text. Accent **marigold** `#E09F3E` (buttons, links,
  highlights) with dark text on it; success **leaf green** `#4F8A4B`; danger **brick**
  `#B5483B`. Follows the system light/dark setting.
- **Type**: system UI font stack (no web font download), larger headings, comfortable
  line height.
- **Layout**: top bar with logo, My jobs / Settings / Admin (owner), user avatar menu
  with Sign out. Cards with soft borders, 12px radius, no heavy shadows. Works at phone
  width.
- **Logo** (first version, to review): a marigold rounded "speech bubble" whose tail
  points down-left, holding a dark magnifier whose handle forms the hook of a "J".
  It reads as "JobHunter finds jobs" and later doubles as the extension bubble. Shipped
  as one SVG (header, favicon, Google sign-in page) plus PNG sizes 16/32/48/128 for
  the extension in B.

## Errors and edge cases
- Invalid, expired or wrong-project token: 401, "Sign-in failed, please try again".
- Unverified Google email: refused.
- Firebase config missing in env: `/login` shows "Sign-in is not set up" and logs which
  variable is missing (names only).
- Certificate fetch fails: sign-in fails with a retry message; already signed-in users
  are unaffected.
- A user deletes all titles: excluded from the run; My jobs shows the empty state.
- Owner email changes: the old owner keeps their data as a normal user; the new email
  becomes owner only if the placeholder is unclaimed. (Changing owners later is a manual
  DB step, documented in the README.)

## Testing
- Token verifier: good token; bad signature; wrong audience; wrong issuer; expired;
  unverified email. Certificates and tokens are generated in the test (no network).
- Sign-in route: creates user; owner claim; blocked; full at 50; CSRF required.
- Isolation: user A cannot see B's settings, Gmail, private jobs, scores or statuses,
  by route and by store function.
- Pipeline: combined titles; per-user scoring; private Gmail jobs; private job becoming
  public; owner-only email.
- Migration: existing single-user data ends up under the owner.
- These are database tests, so the local `TEST_DATABASE_URL` login problem must be
  fixed first (URL-encode the password). This is a prerequisite.
- `ruff check` and `pytest` pass before done.

## Rollout
1. Fix `TEST_DATABASE_URL` locally.
2. Owner adds `OWNER_EMAIL` on Render and in `.env`. The daily run does not need it:
   it finds the owner by `users.is_owner`.
3. Render's build command becomes `pip install -e ".[web]" && python -m jobhunter migrate`,
   so the new pages never start against the old tables. Push; the build applies 0005.
4. Owner signs in with Google first and claims existing data.
5. Remove `WEB_PASSWORD` from Render.
