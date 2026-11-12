# Phase 5: Email actions and "Not interested" reasons (design)

Status: approved in conversation 2026-11-13, waiting for written-spec review.

## Goal

Act on a job straight from the daily email, with no login, and teach JobHunter what to show
less of when a job is not wanted, in a way the user can see and undo.

## Decisions (from the user)

| Topic | Decision |
|---|---|
| Email buttons | One click, no login: **Save**, **Applied**, **Not interested** per job |
| Learning | "Not interested" offers optional one-tap reasons, each with a clear effect |
| Wrong location | Hides jobs in that city (remote jobs never affected) |

Phase 6 (resume and cover-letter drafts) is a separate spec.

## 1. Signed action links

- Each job in the digest gets three links: `{PUBLIC_URL}/a/{token}` for `save`, `applied`,
  `not_interested`.
- The token is signed with `itsdangerous.URLSafeTimedSerializer` (already a dependency) and
  carries `{"u": user_id, "j": job_id, "a": action}`. Its key is derived from `SETTINGS_KEY`
  (HMAC-SHA256 with the label `"email-actions-v1"`), which GitHub Actions (builds the email)
  and Render (receives the click) both have: no new secret.
- Tokens expire after **30 days**. A bad, changed or expired token shows "This link has
  expired, open My jobs instead." (HTTP 400), never an error page.
- New setting `PUBLIC_URL` (default `https://jobhunter-settings.onrender.com`), used to build
  the links. Emails built without it still send, just without action links.
- **Scanners:** `GET /a/{token}` changes nothing. It returns a small page whose script
  immediately submits a form (`POST /a/{token}`, field `confirm=1`); with scripts off the page
  shows a **Confirm** button. Link scanners only fetch the GET, so they never act. The token
  itself is the authorisation, so no session or CSRF cookie is involved.
- `POST /a/{token}` applies the action through the same code as My jobs (`set_status`:
  `saved`, `applied`, `hidden`), only if the job is visible to that user and the user is not
  blocked and still invited. It is idempotent. The result page says "Saved ✓" / "Marked as
  applied ✓" / "Hidden ✓" with a link to My jobs. No session cookie is created.
- Pages are `Cache-Control: no-store` and `Referrer-Policy: no-referrer`. The token is part of
  the address, so it appears in Render's request log, which only the owner can see; it acts
  on one job only and expires.

## 2. "Not interested" reasons

After hiding, the page (and the web app's **Not interested** button, renamed from Hide) offers
optional reasons, each one POST with the same token (email) or the session (web):

| Reason | Stored as | Effect |
|---|---|---|
| Don't like this company | company name in `avoid.companies` | jobs from that company fail the filters ("you avoid this company") |
| Wrong skills | the job's skills not in the profile, added to `avoid.skills` | each avoided skill in a job lowers fit by 0.1, at most 0.3 |
| Wrong location | the job's city in `avoid.cities` | non-remote jobs in that city fail the filters ("you avoid <city>") |
| Not my kind of role | the job's title (normalised) in `avoid.titles` | jobs whose title matches it fully are lowered by 0.2 |

- Stored per user in `user_settings` key `avoid` = `{companies, skills, cities, titles}`
  (lists, case-insensitive, deduplicated, at most 100 entries each).
- Choosing a reason re-scores the user's jobs at once (same as Phase 4 saves).
- Reasons with nothing to add (no city, no unknown skills) are not offered for that job.
- The reason line shows any lowering: "… · Lowered: you avoid AWS" or "… · Lowered: role
  like one you rejected".
- Settings gets a **Hidden and lowered** card listing each list with **Remove** per entry.
- Company comparison is case-insensitive and ignores legal suffixes (Pvt, Ltd, Inc, LLP,
  Private Limited) using the existing normaliser.

## 3. Where it hooks in

- Filters (`matching/filters.py` via `pipeline.best_result`): avoided companies and cities
  fail the job with a readable reason.
- Fit (`matching/fit.py`): `local_fit` takes avoided skills and a "rejected role" flag;
  penalties apply after the skill/title parts and before clamping; levels recompute.
- Digest email: action links under each job (HTML: three small links; text: three URLs).
- The extension's "check" uses the same filters and fit, so it reflects avoids too.

## Privacy and safety

- A token only lets someone holding that email act on that one job for that one user; it
  never signs anyone in or reveals data beyond the job title already in the email.
- Avoid lists are per user, filtered by `user_id`.

## Testing

- Token round trip, tamper, expiry (fake clock), wrong action, other user's job (not visible)
  → refused; blocked or uninvited user → refused.
- GET changes nothing; POST applies once; repeated POST is harmless; no session cookie set.
- Each reason stores the right entries and changes scores/filters as in the table; reasons
  with nothing to add are not shown; Remove undoes them.
- Digest HTML/text contain three links per job with valid tokens; no links without
  `PUBLIC_URL`; HTML escapes everything.
- Web "Not interested" offers the same reasons.
