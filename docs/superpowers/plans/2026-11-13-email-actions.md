# Email Actions and "Not interested" Reasons (Phase 5) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One-click Save / Applied / Not interested links in the daily email, and optional "Not interested" reasons that hide or lower similar jobs, visible and undoable on Settings.

**Architecture:** `actions.py` signs and reads 30-day tokens (itsdangerous, key derived from `SETTINGS_KEY`). `avoid.py` stores per-user avoid lists and turns a reason into entries. `pipeline.best_result` applies avoided companies/cities as filter failures; `fit.local_fit` applies avoided skills and rejected roles as penalties. The digest gets action links; the web app gets `/a/{token}` pages (GET shows, script POSTs), reason pages, and a Settings card.

**Tech Stack:** Python 3.12, FastAPI/Jinja2, itsdangerous, SQLAlchemy; no new dependencies.

**Spec:** `docs/superpowers/specs/2026-11-13-email-actions-design.md`

## Global Constraints

- Actions: `save` → status `saved`, `applied` → `applied`, `not_interested` → `hidden`.
- Token: `URLSafeTimedSerializer`, payload `{"u": user_id, "j": job_id, "a": action}`, key = HMAC-SHA256(SETTINGS_KEY, `"email-actions-v1"`), max age **30 days**.
- Expired/invalid copy: "This link has expired, open My jobs instead." (HTTP 400).
- Result copy: "Saved ✓" / "Marked as applied ✓" / "Hidden ✓".
- Reasons and copy: `company` "Don't like this company", `skills` "Wrong skills", `location` "Wrong location", `role` "Not my kind of role".
- Effects: company → fail "you avoid this company"; location → non-remote jobs in that city fail "you avoid <City>"; skills → −0.1 per avoided skill in the job, max −0.3; role → −0.2 when `title_score(job title, avoid.titles) == 1.0`.
- Reason line suffix: `" · Lowered: you avoid A, B"` (skills, ≤ 3 names) and/or `" · Lowered: role like one you rejected"`.
- `user_settings` key `avoid` = `{"companies", "skills", "cities", "titles"}`, each ≤ 100 entries, case-insensitive dedupe; companies compared with `normalize._company_key`.
- `PUBLIC_URL` setting, default `https://jobhunter-settings.onrender.com`.
- GET `/a/{token}` never changes data; POST requires field `confirm=1`. Pages: `Cache-Control: no-store`, `Referrer-Policy: no-referrer`; no session cookie set. CSP forbids inline script, so the auto-submit lives in `static/act.js`.
- Applying an action needs: job visible to the user, user exists, not blocked, still invited.
- `ruff check`, `pytest -q`, `npm test` pass at the end of each task that touches them.

## Review Focus

1. **Link opened by a scanner (GET only)** → nothing recorded. → Task 4 `test_get_changes_nothing`.
2. **Old or forwarded link after the user was removed/blocked** → refused, no change. → Task 4 `test_blocked_or_uninvited_user_refused`.
3. **Link for a job that is private to another user / token edited to another job id** → refused. → Task 1 `test_tampered_token_rejected`, Task 4 `test_job_not_visible_refused`.
4. **Reason that adds nothing** (remote job + Wrong location; job whose skills are all in the profile + Wrong skills) → not offered and, if posted anyway, a harmless no-op. → Task 2 `test_reason_options_only_when_useful`, `test_useless_reason_is_noop`.
5. **Clicking the same link twice / reason twice** → same result, no duplicate entries. → Task 2 `test_entries_deduplicated`, Task 4 `test_repeat_post_is_harmless`.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/jobhunter/actions.py` | `ACTIONS`, `action_key`, `make_token`, `read_token`, `action_url` |
| `src/jobhunter/avoid.py` | `Avoid`, `load_avoid`, `reason_options`, `add_reason`, `remove_entry` |
| `pipeline.py`, `matching/fit.py`, `web/api.py` | apply avoids |
| `notify/email_resend.py`, `__main__.py` | links in the digest |
| `web/app.py`, `web/store.py`, `templates/action.html`, `templates/settings.html`, `templates/jobs.html`, `static/act.js` | pages |

---

### Task 1: Signed action tokens

**Files:** Create `src/jobhunter/actions.py`; Modify `src/jobhunter/settings.py` (`public_url: str = "https://jobhunter-settings.onrender.com"`); Test `tests/test_actions.py`.

**Interfaces:**
- Produces: `ACTIONS = {"save": "saved", "applied": "applied", "not_interested": "hidden"}`; `MAX_AGE = 30 * 86400`; `class TokenInvalid(Exception)`; `@dataclass(frozen=True) Action: user_id: int; job_id: int; action: str`; `action_key(settings_key: str) -> bytes`; `make_token(key: bytes, user_id: int, job_id: int, action: str, now: float | None = None) -> str`; `read_token(key: bytes, token: str, now: float | None = None) -> Action` (raises `TokenInvalid`); `action_url(base_url: str, token: str) -> str` (`f"{base_url.rstrip('/')}/a/{token}"`).

- [ ] **Step 1: Failing tests:** `test_round_trip`; `test_tampered_token_rejected` (flip a char; swap in a token made with another key); `test_expiry` (made at t, read at t + 30 d − 1 → ok, t + 30 d + 1 → `TokenInvalid`); `test_unknown_action_rejected` (`make_token(..., "delete")` raises `ValueError`; a validly signed payload with action "delete" → `TokenInvalid`); `test_key_differs_from_settings_key` (`action_key("k") != b"k"` and is 32 bytes); `test_action_url`.
- [ ] **Step 2:** FAIL. **Step 3:** implement; `now` handled by a `URLSafeTimedSerializer` subclass whose signer's `get_timestamp` returns the given time (pass `signer_kwargs`), so no real clock in tests. **Step 4:** PASS. **Step 5: Commit** `feat: signed one-click action tokens`.

---

### Task 2: Avoid lists and reasons

**Files:** Create `src/jobhunter/avoid.py`; Modify `src/jobhunter/user_settings.py` (`USER_KEYS` += `avoid`), `src/jobhunter/normalize.py` (rename `_company_key` → `company_key`, keep old name as alias); Test `tests/test_avoid.py`.

**Interfaces:**
- Consumes: `Job` row (`company`, `city`, `work_mode`, `title`, `skills`), profile skills (Phase 4 `load_profile`).
- Produces:
  - `REASONS = {"company": "Don't like this company", "skills": "Wrong skills", "location": "Wrong location", "role": "Not my kind of role"}`; `MAX_ENTRIES = 100`.
  - `@dataclass(frozen=True) Avoid: companies: tuple[str, ...] = (); skills: tuple[str, ...] = (); cities: tuple[str, ...] = (); titles: tuple[str, ...] = ()` with methods `avoids_company(company: str) -> bool`, `avoided_city(city: str | None, work_mode: str) -> str | None` (None for remote), `is_empty() -> bool`.
  - `load_avoid(session, user_id) -> Avoid`.
  - `reason_options(job: Job, profile_skills: set[str]) -> list[str]` (keys in `REASONS` order; `skills` only if the job has skills outside the profile; `location` only if `job.city` and `job.work_mode != "remote"`; `company` and `role` always).
  - `add_reason(session, user_id, job: Job, reason: str, profile_skills: set[str]) -> bool` (False and no change for unknown/useless reasons; stores display values: company as written, city title-case, skills canonical, title as written).
  - `remove_entry(session, user_id, kind: str, value: str) -> bool` (`kind` ∈ companies/skills/cities/titles).

- [ ] **Step 1: Failing tests:** `test_reason_options_only_when_useful`; `test_add_each_reason` (company "Acme Pvt Ltd" stored, `avoids_company("ACME")` True; skills = job skills − profile; city "Pune"; title); `test_entries_deduplicated` (same reason twice, different case); `test_useless_reason_is_noop`; `test_cap_100`; `test_remove_entry`; `test_isolated_per_user`.
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** PASS. **Step 5: Commit** `feat: per-user avoid lists from Not interested reasons`.

---

### Task 3: Avoids change filters and fit

**Files:** Modify `src/jobhunter/pipeline.py` (`best_result(job, searches, now, max_age_days, avoid: Avoid | None = None)`; `refilter_for_user` loads the avoid list and passes it, and passes avoid info into fit), `src/jobhunter/matching/fit.py` (`local_fit(..., avoid_skills: set[str] = frozenset(), rejected_role: bool = False)`), `src/jobhunter/web/api.py` (check uses the user's avoid list); Test `tests/test_avoid_scoring.py`.

**Interfaces:**
- Consumes: `Avoid` (Task 2); `title_score` from `matching/filters.py`.
- Produces: avoided company → `FilterResult(False, "you avoid this company", title_score)`; avoided city → `FilterResult(False, f"you avoid {City}", ...)`; fit penalties and reason suffix as in Global Constraints. Avoid penalties also apply without a profile? **No**: without a profile the provisional score is kept, but company/city filters still apply.

- [ ] **Step 1: Failing tests:** `test_avoided_company_fails_filters` (legal-suffix variant too); `test_avoided_city_fails_but_remote_kept`; `test_avoided_skills_lower_fit` (one → −0.1, four → −0.3 cap; reason ends " · Lowered: you avoid AWS"); `test_rejected_role_lowers_fit` (−0.2, suffix); `test_check_api_respects_avoids`; `test_no_avoid_unchanged` (existing Phase 4 numbers unchanged).
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** full `pytest -q` PASS. **Step 5: Commit** `feat: avoided companies, cities, skills and roles affect matches`.

---

### Task 4: Action pages (`/a/{token}`)

**Files:** Modify `src/jobhunter/web/app.py` (routes, `ActionGateway` protocol + `DbActions`), `src/jobhunter/web/store.py` or new `src/jobhunter/web/actions_store.py` (`DbActions`); Create `templates/action.html`, `static/act.js`; Test `tests/test_web_actions.py`.

**Interfaces:**
- Consumes: `read_token`, `action_key`, `ACTIONS` (Task 1); `reason_options`, `add_reason`, `REASONS` (Task 2); `JobsStore.set_status`; `Access.invited`.
- Produces: `class ActionGateway(Protocol)`: `apply(user_id: int, job_id: int, status: str) -> JobBrief | None` (None = refused) and `add_reason(user_id: int, job_id: int, reason: str) -> bool`, `reasons_for(user_id, job_id) -> list[str]`; `JobBrief(title: str, company: str)`. `create_app(..., actions: ActionGateway | None = None)`.
  - `GET /a/{token}` → 200 page with a form `POST /a/{token}` (`confirm=1`) and `<script src="/static/act.js">` that submits it; invalid token → 400 expired copy.
  - `POST /a/{token}` without `confirm=1` → 400; with → apply; refused → 400 expired copy; ok → result copy + job title; for `not_interested` also reason buttons (`POST /a/{token}/reason`, field `reason`).
  - `POST /a/{token}/reason` → `add_reason`; page "Got it. Similar jobs will be hidden or lowered." with a link to Settings.
  - All `/a/` responses: `no-store`, `no-referrer`, no `Set-Cookie`.

- [ ] **Step 1: Failing tests:** `test_get_changes_nothing`; `test_post_applies_each_action` (three actions → statuses saved/applied/hidden); `test_repeat_post_is_harmless`; `test_expired_or_bad_token_page`; `test_job_not_visible_refused`; `test_blocked_or_uninvited_user_refused`; `test_not_interested_offers_reasons_and_records_one`; `test_no_cookie_and_headers`; `test_act_js_submits_form` (static file served, contains `submit()`), `test_post_needs_confirm`.
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** `ruff check`, `pytest -q` PASS. **Step 5: Commit** `feat(web): one-click action pages for email links`.

---

### Task 5: Links in the daily email

**Files:** Modify `src/jobhunter/notify/email_resend.py` (`build_digest(jobs, now, links: Callable[[int], dict[str, str]] | None = None)`, `deliver_digest(..., links=None)`), `src/jobhunter/__main__.py` (`_deliver` builds `links` from `settings.public_url`, `action_key(settings_key)`, `make_token` for this user; no links when `SETTINGS_KEY` or `PUBLIC_URL` is empty), `.github/workflows/run.yml` (optional `PUBLIC_URL` env from secrets, falls back to default); Test extend `tests/test_email.py`, `tests/test_pipeline_users.py` or a new `tests/test_digest_links.py`.

**Interfaces:**
- Consumes: `make_token`, `action_url` (Task 1).
- Produces: per job in HTML a line "Save · Applied · Not interested" (three `<a>`); in text three lines `  Save: <url>` etc.

- [ ] **Step 1: Failing tests:** `test_digest_has_three_links_per_job` (URLs start with PUBLIC_URL/a/, tokens decode to the right user/job/action); `test_no_links_without_function`; `test_deliver_builds_links_for_that_user` (two users → tokens carry each user's id); `test_links_escaped_in_html`.
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** PASS. **Step 5: Commit** `feat: Save / Applied / Not interested links in the digest`.

---

### Task 6: Not interested on My jobs, and the Settings card

**Files:** Modify `templates/jobs.html` (Hide → "Not interested"; after it, a reasons page), `web/app.py` (`POST /jobs/{id}/not-interested` → hide + reasons page; `POST /jobs/{id}/reason`; `POST /avoid/remove` with `kind`, `value`), `web/store.py` (`load_avoid() -> Avoid`, `add_reason(job_id, reason) -> bool`, `remove_avoid(kind, value) -> bool`, each re-scores), `templates/settings.html` (card "Hidden and lowered", id `avoid`), FakeStore in `tests/test_web_app.py`; Test `tests/test_web_avoid.py`.

**Interfaces:**
- Consumes: Task 2 functions; Task 4 reasons page template (shared partial).
- Produces: Settings card listing four lists with **Remove** per entry; empty state "Nothing hidden or lowered yet."

- [ ] **Step 1: Failing tests:** `test_not_interested_button_hides_and_offers_reasons`; `test_reason_from_web_recorded`; `test_settings_card_lists_and_removes`; `test_avoid_routes_need_csrf_and_sign_in`; DB test `test_store_reason_rescores_at_once`.
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** `ruff check`, `pytest -q`; screenshots of the reasons page and Settings card at 1280 and 375 px. **Step 5: Commit** `feat(web): Not interested reasons on My jobs; Hidden and lowered card`.

---

### Task 7: Docs

**Files:** `README.md` (email actions, reasons table, `PUBLIC_URL`; roadmap 5 Done), `CLAUDE.md` (rule: action tokens signed with a key derived from SETTINGS_KEY, 30 days, GET never changes data).

- [ ] **Step 1:** edit. **Step 2:** `ruff check`, `pytest -q`, `npm test` PASS. **Step 3: Commit** `docs: Phase 5 email actions`.
