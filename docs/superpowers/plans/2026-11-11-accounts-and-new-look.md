# Accounts (Google sign-in) and New Look Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the single-password, single-user JobHunter into a multi-user app with Firebase Google sign-in, per-user settings/Gmail/matches, a My jobs page, an owner Admin page, and a new marigold look.

**Architecture:** Jobs stay one shared table fetched once a day with a combined search built from every user's titles; everything personal (profiles, settings, Gmail, scores, statuses, private Gmail jobs) gets a `user_id`. The web app verifies Firebase ID tokens itself (RS256 against Google's public certs) and keeps `user_id` in the signed session cookie.

**Tech Stack:** Python 3.12+, SQLAlchemy 2, Alembic, psycopg 3, FastAPI + Jinja2, PyJWT[crypto] (new), Firebase JS SDK (modular, pinned, from gstatic), pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-11-11-accounts-and-new-look-design.md`

## Global Constraints

- Owner = verified Google email equal (case-insensitive) to env `OWNER_EMAIL`; claims user id 1 (owner placeholder, `firebase_uid` NULL).
- At most 50 users (`MAX_USERS = 50`; the owner always gets in). At most 15 titles per user (`MAX_TITLES = 15`).
- Firebase env: `FIREBASE_API_KEY`, `FIREBASE_AUTH_DOMAIN`, `FIREBASE_PROJECT_ID`, `FIREBASE_APP_ID`. Token checks: RS256; `aud == FIREBASE_PROJECT_ID`; `iss == "https://securetoken.google.com/" + project`; not expired; `iat` not in future; `sub` non-empty; `email_verified` true. Certs from `https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com`, cached per `Cache-Control: max-age`.
- `WEB_PASSWORD` is removed. `SESSION_SECRET` (32+ chars) stays. Session lasts 30 days.
- `job_status.status` values: `new`, `saved`, `applied`, `hidden`.
- Only the owner gets the email digest unless `app_settings.digest_from` is set to an address not ending in `@resend.dev`; then every user with `digest_to` gets one.
- Palette: light bg `#FAF7F2`, ink `#1F1B16`, muted `#6B6258`, border `#E6DFD5`; dark bg `#16130F`, card `#201C17`, text `#F2ECE3`; accent marigold `#E09F3E` (dark text on it), success `#4F8A4B`, danger `#B5483B`. No blue anywhere. System font stack. 12px radius.
- Every per-user query filters by `user_id`. No network in tests. Never log tokens, app passwords or `.env` values.
- `ruff check` and `pytest` (all tests, including DB tests against `TEST_DATABASE_URL`) pass at the end of every task.

## Review Focus

1. **Cross-user leakage through IDs in URLs/forms**: user B posting `/jobs/<id>/status` for a job private to user A, or for a job id that does not exist, must get 404 and change nothing. → Task 7 test `test_status_on_someone_elses_private_job_is_404`.
2. **Same Google account, second sign-in**: must reuse the user (by `firebase_uid`), update email/name/last_login, never create a duplicate or count against the 50 cap. → Task 2 test `test_second_sign_in_reuses_user`.
3. **Owner signs in when 50 others already exist**: still gets in and claims the placeholder. → Task 2 test `test_owner_gets_in_when_full`.
4. **A blocked user with a live session cookie**: next request is sent to `/blocked`, not served. → Task 6 test `test_blocked_with_existing_session_is_logged_out`.
5. **User with no titles yet** (every new user): excluded from the run's combined search, My jobs shows the empty state pointing to Settings, Settings page loads with defaults. → Task 5 test `test_users_without_titles_are_skipped`, Task 7 test `test_my_jobs_empty_state`.

---

## File map

| File | Responsibility |
|---|---|
| `alembic/versions/0005_users.py` | new tables, new columns, PK changes, owner placeholder, move data to user 1 |
| `src/jobhunter/db.py` | models `User`, `UserSetting`, `JobOwner`; `user_id` on `SearchProfile`, `MailAccount`, `JobScore`, `JobStatus`; `Job.private` |
| `src/jobhunter/accounts.py` (new) | sign-in outcome, owner claim, caps, block/unblock, list users |
| `src/jobhunter/web/firebase_auth.py` (new) | Firebase ID-token verifier with injectable cert fetcher |
| `src/jobhunter/user_settings.py` | per-user settings get/set; `load_active_searches(session, user_id)`; seeding a new user |
| `src/jobhunter/mail_account.py` | all functions take `user_id`; `gmail_adapters(session, key)` for the run |
| `src/jobhunter/pipeline.py` | `combined_search`, private upserts, per-user refilter/digest/mark, visibility |
| `src/jobhunter/sources/registry.py` | no Gmail here any more (Gmail is per user) |
| `src/jobhunter/__main__.py` | `cmd_run` loops users for Gmail, filtering and email |
| `src/jobhunter/web/store.py` | `UserStore` (per user) and `AdminStore` (owner) |
| `src/jobhunter/web/jobs_store.py` (new) | My jobs queries and status changes |
| `src/jobhunter/web/app.py` | routes, auth guard, CSP per page |
| `src/jobhunter/web/templates/*` | `base`, `login`, `jobs`, `settings`, `admin`, `notice` |
| `src/jobhunter/web/static/` (new) | `app.css`, `logo.svg`, `favicon.svg`, `login.js` |

---

### Task 1: Schema for users (migration 0005 + models)

**Files:**
- Create: `alembic/versions/0005_users.py`
- Modify: `src/jobhunter/db.py`
- Test: `tests/test_migration_users.py`

**Interfaces:**
- Produces (in `jobhunter.db`):
  - `User`: `id` int PK, `firebase_uid` text unique nullable, `email` text nullable, `name` text nullable, `is_owner` bool default false, `blocked` bool default false, `created_at`, `last_login_at` timestamptz.
  - `UserSetting`: `user_id` FK users (cascade) + `key` text = PK, `value` JSONB.
  - `JobOwner`: `job_id` FK jobs (cascade) + `user_id` FK users (cascade) = PK.
  - `Job.private` bool server_default false.
  - `SearchProfile.user_id`, `MailAccount.user_id` (unique), `JobScore.user_id`, `JobStatus.user_id`: int FK users (cascade), not null. `JobScore` and `JobStatus` PK = (`user_id`, `job_id`).
  - `OWNER_PLACEHOLDER_ID = 1`.
- Migration upgrade: create `users`, insert row id 1 (`is_owner` true, `firebase_uid` NULL) and set the sequence past it; add columns with `server_default='1'` then drop the default; move `app_settings` keys `digest`, `digest_to`, `max_age_days` into `user_settings` for user 1 and delete them from `app_settings`; swap PKs on `job_scores`/`job_status`. Downgrade reverses (keeping user 1's rows only).

- [ ] **Step 1: Write the failing test** `test_upgrade_moves_single_user_data_to_owner`: on a fresh schema in `TEST_DATABASE_URL` (same schema trick as `tests/conftest.py`, passing the URL with `options=-csearch_path=<schema>` to alembic via `Config.set_main_option("sqlalchemy.url", ...)`), `command.upgrade(cfg, "0004")`, insert one `search_profiles` row, one `mail_accounts` row, one job with a `job_scores` and `job_status` row, and `app_settings` rows `digest`, `digest_to`, `max_age_days`, `sources`; then `command.upgrade(cfg, "head")`. Assert: `users` has exactly id 1 with `is_owner` true and NULL uid; all four per-user rows have `user_id == 1`; `user_settings` has the three moved keys for user 1; `app_settings` still has `sources` but not the three moved keys.
- [ ] **Step 2: Add** `test_models_match_migration`: after `upgrade head`, the column names of every table in `Base.metadata` equal those in the database (via `sqlalchemy.inspect`).
- [ ] **Step 3: Run** `pytest tests/test_migration_users.py -v` → FAIL (no revision 0005).
- [ ] **Step 4: Implement** the migration and the models.
- [ ] **Step 5: Run** `pytest -q` → all pass (existing DB tests will need `user_id=1` added where they create profiles/scores/status; update them in this task: they use `create_all`, so insert a `User(id=1, is_owner=True)` in a shared fixture `owner_user` in `tests/conftest.py`).
- [ ] **Step 6: Commit** `feat(db): users table and per-user columns (migration 0005)`.

### Task 2: Accounts (sign-in outcome, owner claim, caps, blocking)

**Files:**
- Create: `src/jobhunter/accounts.py`
- Test: `tests/test_accounts.py`

**Interfaces:**
- Consumes: Task 1 models.
- Produces:
  - `MAX_USERS = 50`
  - `@dataclass(frozen=True) class Identity: uid: str; email: str; name: str | None`
  - `@dataclass(frozen=True) class SignIn: outcome: Literal["ok", "blocked", "full"]; user_id: int | None`
  - `sign_in(session, identity: Identity, owner_email: str, now: datetime, max_users: int = MAX_USERS) -> SignIn`
  - `get_user(session, user_id: int) -> User | None`
  - `list_users(session) -> list[UserRow]` where `UserRow(id, email, name, is_owner, blocked, created_at, last_login_at, titles: int, gmail: bool)`
  - `set_blocked(session, user_id: int, blocked: bool) -> None` (raises `ValueError` for the owner)

Rules in `sign_in`, in order: existing uid → update email/name/last_login → `blocked` or `ok`. Else if email matches `owner_email` and user 1 is unclaimed → claim it. Else if non-owner count (users with a uid, excluding owner) ≥ `max_users` → `full`. Else create user and seed a default profile (Task 4 provides `seed_user`; until then create the `User` only — Task 4 adds the call).

- [ ] **Step 1: Write failing tests**: `test_new_user_created`, `test_second_sign_in_reuses_user` (same uid, new email → same id, email updated, user count unchanged), `test_owner_claims_placeholder` (owner email in different case → `user_id == 1`, uid stored), `test_owner_email_ignored_when_unset` (`owner_email=""` → new user, not 1), `test_full_at_cap` (`max_users=2`, 2 users exist → `full`, nothing created), `test_owner_gets_in_when_full`, `test_blocked_user_refused`, `test_cannot_block_owner`.
- [ ] **Step 2: Run** `pytest tests/test_accounts.py -v` → FAIL.
- [ ] **Step 3: Implement** `accounts.py`.
- [ ] **Step 4: Run** `pytest -q` → PASS.
- [ ] **Step 5: Commit** `feat: accounts with owner claim, user cap and blocking`.

### Task 3: Firebase ID-token verifier

**Files:**
- Create: `src/jobhunter/web/firebase_auth.py`
- Modify: `pyproject.toml` (`web` extra gets `PyJWT[crypto]>=2.8`)
- Test: `tests/test_firebase_auth.py`

**Interfaces:**
- Produces:
  - `class TokenError(Exception)` (message safe to show; never contains the token)
  - `CERTS_URL` constant (Global Constraints)
  - `CertFetcher = Callable[[], tuple[dict[str, str], int]]` returning (`kid -> PEM cert`, max-age seconds)
  - `class FirebaseVerifier(project_id: str, fetch: CertFetcher | None = None, clock: Callable[[], float] = time.time)`; `.verify(token: str) -> Identity` (Identity from Task 2). Default fetcher uses httpx GET on `CERTS_URL`, parses `Cache-Control` `max-age`. Certs cached until `clock() >= fetched_at + max_age`; an unknown `kid` forces one refetch.

- [ ] **Step 1: Write failing tests** generating an RSA key and self-signed cert with `cryptography` in a fixture and signing tokens with `jwt.encode(..., algorithm="RS256", headers={"kid": "k1"})`: `test_valid_token`, `test_bad_signature`, `test_wrong_audience`, `test_wrong_issuer`, `test_expired`, `test_issued_in_future`, `test_unverified_email`, `test_missing_sub`, `test_hs256_token_rejected` (algorithm confusion), `test_certs_cached_until_max_age` (fetch called once for two verifies, again after clock passes max-age), `test_unknown_kid_refetches_once`, `test_error_message_has_no_token`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**; `jwt.decode(token, key, algorithms=["RS256"], audience=project_id, issuer=..., options={"require": ["exp", "iat", "sub"]}, leeway=60)`, then the `iat`/`email_verified`/`sub` checks; map every `jwt` exception to `TokenError("Sign-in failed, please try again.")`.
- [ ] **Step 4: Ask the user to run** `.\.venv\Scripts\python.exe -m pip install -e ".[web]"` if `jwt` is missing (they installed `PyJWT[crypto]` already). Run `pytest -q` → PASS.
- [ ] **Step 5: Commit** `feat(web): verify Firebase ID tokens without a private key`.

### Task 4: Per-user settings, profiles and Gmail

**Files:**
- Modify: `src/jobhunter/user_settings.py`, `src/jobhunter/mail_account.py`, `src/jobhunter/web/store.py`, `src/jobhunter/web/forms.py`, `src/jobhunter/accounts.py` (call `seed_user`)
- Test: `tests/test_user_isolation.py`, update `tests/test_web_store.py`, `tests/test_user_settings.py`

**Interfaces:**
- Produces:
  - `user_settings.get_user_setting(session, user_id, key, default=None)`, `set_user_setting(session, user_id, key, value)`
  - `user_settings.seed_user(session, user_id) -> None` (one active profile from `BUILTIN_PROFILE` with empty titles, if the user has none)
  - `user_settings.load_active_searches(session, user_id: int) -> list[Search]`
  - `user_settings.active_user_searches(session) -> dict[int, list[Search]]` (users not blocked, profiles active, titles non-empty)
  - `mail_account`: `get_account(session, user_id)`, `mail_view(session, user_id)`, `save_account(session, user_id, address, app_password, senders, key)`, `delete_account(session, user_id)`, `gmail_adapter(session, user_id, key, **kw)`, `gmail_adapters(session, key) -> list[tuple[int, GmailAlertsAdapter | BrokenSource]]` (skips blocked users; `CryptoError` → `BrokenSource("gmail_alerts", msg)`)
  - `web.store.UserStore(session_factory, user_id: int, settings_key: str)` with today's `load`, `save`, `load_mail`, `save_mail`, `delete_mail`, `test_mail`, `recent_runs` (shared runs plus only this user's `gmail_alerts:<user_id>` rows; other users' Gmail rows never shown); `ProfileForm` loses `sources`/`watchlist`.
  - `web.store.AdminStore(session_factory)` with `load_sources() -> dict[str, bool]`, `load_watchlist() -> list[Company]`, `save_sources_and_watchlist(sources, watchlist)`, `users() -> list[UserRow]`, `set_blocked(user_id, blocked)`.
  - `web.forms.MAX_TITLES = 15`; `parse_form(fields, work_modes)` (no sources); new `parse_admin_form(fields, sources) -> (dict[str,bool], list[Company], errors)`.

- [ ] **Step 1: Write failing tests** in `test_user_isolation.py`: `test_runs_hide_other_users_gmail_rows`, `test_profiles_are_per_user` (A saves titles, B loads defaults), `test_user_settings_are_per_user` (max_jobs), `test_gmail_is_per_user` (A saves Gmail; B's `load_mail()` is empty; B's `delete_mail()` leaves A's), `test_active_user_searches_skips_blocked_and_titleless`, `test_new_user_gets_default_profile` (via `accounts.sign_in`); in `test_web_forms.py`: `test_more_than_15_titles_rejected` (error text `"At most 15 job titles."`).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**; migrate existing callers (`__main__` keeps compiling: pass `OWNER_PLACEHOLDER_ID` until Task 5 rewrites it).
- [ ] **Step 4: Run** `pytest -q` → PASS.
- [ ] **Step 5: Commit** `feat: per-user settings, profiles and Gmail`.

### Task 5: Multi-user daily run

**Files:**
- Modify: `src/jobhunter/pipeline.py`, `src/jobhunter/sources/registry.py`, `src/jobhunter/__main__.py`
- Test: `tests/test_pipeline_users.py`, update `tests/test_pipeline.py`, `tests/test_cli_run.py`, `tests/test_registry.py`

**Interfaces:**
- Consumes: Task 4 `active_user_searches`, `gmail_adapters`.
- Produces (in `pipeline`):
  - `combined_search(per_user: dict[int, list[Search]]) -> Search | None`: titles deduped case-insensitively, ordered by how many users have them (desc) then alphabetically; `cities=[]`, all work modes, `min_years=min`, `max_years=max`, `exclude_words=[]`. `None` when no titles.
  - `ingest_source(session, adapter, searches, now, private_to: int | None = None) -> SourceResult` (`Run.source` = `"gmail_alerts:<user_id>"` when private).
  - `upsert_jobs(session, jobs, now, private_to: int | None = None) -> int`: public upsert sets `private=false` on conflict; private insert sets `private=true` and inserts `job_owners(job_id, private_to)` on conflict do nothing; a private upsert never flips a public job to private.
  - `visible_jobs(user_id) -> Select` helper used by refilter, digest and My jobs.
  - `refilter_for_user(session, user_id, searches, now, max_age_days) -> tuple[int, int]` (skips jobs with that user's `emailed_at` set or status `hidden`)
  - `select_digest(session, user_id, now, max_age_days, max_jobs) -> list[DigestJob]`; `mark_emailed(session, user_id, job_ids, now)`
  - `run_pipeline(session, shared_adapters, gmail: list[tuple[int, SourceAdapter]], now=None) -> RunSummary` where `RunSummary.users: dict[int, UserResult(scored, passed, digest)]`; per-user `max_age_days`/`max_jobs` read from `user_settings` (defaults 14 / 25).
  - `__main__.cmd_run`: emails the owner's digest; emails other users only when `digest_from` is set and does not end with `@resend.dev`; one user's delivery failure is reported and does not stop others. `ran_successfully_today` unchanged (one email run row per day for the owner).

- [ ] **Step 1: Write failing tests**: `test_combined_search_orders_by_popularity`, `test_users_without_titles_are_skipped`, `test_job_passes_for_one_user_not_other` (A Delhi, B Pune, same job → only A has `passed_filters`), `test_gmail_job_private_to_owner` (B cannot see it in `select_digest`), `test_private_job_becomes_public_when_seen_publicly`, `test_second_user_same_gmail_job_gets_access`, `test_hidden_job_not_in_digest`, `test_only_owner_emailed_without_domain` (cli test with fake sender: one send), `test_all_users_emailed_with_domain` (`digest_from="jobs@example.com"` → one send per user with `digest_to`), `test_one_users_gmail_failure_does_not_stop_others`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**; remove Gmail from `build_adapters`.
- [ ] **Step 4: Run** `pytest -q` → PASS.
- [ ] **Step 5: Commit** `feat: shared fetch, per-user filtering and digests`.

### Task 6: Google sign-in in the web app

**Files:**
- Modify: `src/jobhunter/settings.py` (drop `web_password`; add `firebase_api_key`, `firebase_auth_domain`, `firebase_project_id`, `firebase_app_id`, `owner_email`), `src/jobhunter/web/app.py`
- Create: `src/jobhunter/web/static/login.js`, `src/jobhunter/web/templates/notice.html`; rewrite `templates/login.html`
- Test: rewrite `tests/test_web_app.py` auth parts; `tests/test_web_auth.py`

**Interfaces:**
- Consumes: Task 2 `sign_in`, `get_user`; Task 3 `FirebaseVerifier.verify`.
- Produces:
  - `create_app(store_factory: Callable[[int], UserStore] | None = None, admin_store: AdminStore | None = None, jobs_store: JobsStore | None = None, verifier: FirebaseVerifier | None = None, accounts: AccountsGateway | None = None, settings: Settings | None = None)`. `AccountsGateway` = small Protocol with `sign_in(identity) -> SignIn` and `get_user(user_id) -> UserInfo | None` (`UserInfo(id, email, name, is_owner, blocked)`), DB-backed by default, faked in tests.
  - `current_user(request) -> UserInfo | None`: reads `session["uid"]`, loads the user each request; blocked → clears session.
  - Routes: `GET /login` (Firebase config rendered into `data-*` attributes; if any `FIREBASE_*` missing → page says "Sign-in is not set up" and logs the missing names), `POST /auth/session` (JSON `{"idToken", "csrf"}` → 200 `{"next": "/"}`, 401 `{"error"}`, 403 bad CSRF; outcome `blocked` → `{"next": "/blocked"}`, `full` → `{"next": "/full"}`), `POST /logout`, `GET /blocked`, `GET /full`.
  - `login.js`: imports `firebase-app.js` and `firebase-auth.js` from `https://www.gstatic.com/firebasejs/10.14.1/`; `signInWithPopup(auth, new GoogleAuthProvider())` → `getIdToken()` → POST → `signOut(auth)` → `location = next`; shows the error text from the server.
  - CSP on `/login` only adds: `script-src 'self' https://www.gstatic.com https://apis.google.com; connect-src 'self' https://identitytoolkit.googleapis.com https://securetoken.googleapis.com; frame-src https://<FIREBASE_AUTH_DOMAIN> https://accounts.google.com`. Other pages keep today's CSP plus `img-src 'self' data: https://lh3.googleusercontent.com` (avatars).
  - `SESSION_DAYS = 30`; on success `session.clear()` then `session["uid"] = user_id` and a fresh CSRF token.

- [ ] **Step 1: Write failing tests** (fake verifier and gateway): `test_login_page_has_firebase_config`, `test_login_page_when_not_configured`, `test_session_created_from_token`, `test_bad_token_401_and_no_session`, `test_session_requires_csrf`, `test_blocked_outcome_goes_to_blocked`, `test_full_outcome_goes_to_full`, `test_blocked_with_existing_session_is_logged_out`, `test_pages_require_sign_in` (`/`, `/settings`, `/admin` → 303 `/login`), `test_logout_clears_session`, `test_login_csp_allows_firebase_only_on_login`, `test_no_web_password_needed` (app starts with only `SESSION_SECRET`).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest -q` → PASS.
- [ ] **Step 5: Commit** `feat(web): Google sign-in with Firebase replaces the web password`.

### Task 7: My jobs page

**Files:**
- Create: `src/jobhunter/web/jobs_store.py`, `src/jobhunter/web/templates/jobs.html`
- Modify: `src/jobhunter/web/app.py`
- Test: `tests/test_jobs_store.py` (DB), `tests/test_web_jobs.py`

**Interfaces:**
- Consumes: Task 5 `visible_jobs`.
- Produces:
  - `JobCard(job_id, title, company, location_text, work_mode, source, posted_at, url, status, score)`
  - `JobsStore(session_factory)`: `list(user_id, tab: Literal["matches", "saved", "applied"], limit=100) -> list[JobCard]` (matches = passed filters, not hidden/applied, within that user's `max_age_days`, best score then newest; saved/applied = by status, newest change first), `set_status(user_id, job_id, status) -> bool` (False when the job is not visible to the user or status invalid).
  - Routes: `GET /` (tab from `?tab=`), `POST /jobs/{job_id}/status` (form `status`, CSRF) → 303 back to the tab; 404 when `set_status` is False.

- [ ] **Step 1: Write failing tests**: `test_matches_best_first`, `test_hidden_and_applied_leave_matches`, `test_saved_tab`, `test_status_on_someone_elses_private_job_is_404`, `test_status_on_missing_job_is_404`, `test_invalid_status_rejected`, `test_my_jobs_empty_state` (text `"Add the job titles you want on Settings"` and a link to `/settings`), `test_job_links_open_in_new_tab_with_noopener`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest -q` → PASS.
- [ ] **Step 5: Commit** `feat(web): My jobs page with save, applied and hide`.

### Task 8: Settings per user and owner Admin page

**Files:**
- Modify: `src/jobhunter/web/app.py`, `templates/settings.html`
- Create: `templates/admin.html`
- Test: `tests/test_web_settings.py`, `tests/test_web_admin.py`

**Interfaces:**
- Consumes: Task 4 `UserStore`, `AdminStore`, `parse_form`, `parse_admin_form`.
- Produces: `GET/POST /settings`, `POST /gmail`, `/gmail/test`, `/gmail/delete` (now per signed-in user; redirect targets become `/settings...`); `GET /admin`, `POST /admin/sources`, `POST /admin/users/{user_id}/block` (form `blocked=1|0`). Non-owner on any `/admin*` → 404. Settings no longer shows sources/watchlist; Admin shows users table, sources checkboxes and watchlist textarea.

- [ ] **Step 1: Write failing tests**: existing settings/Gmail tests moved to `/settings` with a signed-in fake user; `test_two_users_see_their_own_settings`; `test_admin_hidden_from_non_owner` (404); `test_owner_blocks_user`; `test_owner_cannot_block_self` (400, message `"You can't block the owner."`); `test_admin_saves_sources_and_watchlist`; `test_bad_watchlist_keeps_what_you_typed` (moved from settings).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest -q` → PASS.
- [ ] **Step 5: Commit** `feat(web): per-user settings and owner admin page`.

### Task 9: New look and logo

**Files:**
- Create: `src/jobhunter/web/static/app.css`, `static/logo.svg`, `static/favicon.svg`
- Modify: `templates/base.html` (top bar: logo, My jobs / Settings / Admin (owner), avatar + Sign out; links `app.css`; favicon), every template's markup to the new classes; `app.py` mounts `/static` with `StaticFiles`
- Test: `tests/test_web_look.py`

**Interfaces:**
- Produces: CSS custom properties on `:root` named `--bg --card --ink --muted --border --accent --accent-ink --ok --danger`, light values by default and dark under `@media (prefers-color-scheme: dark)`, values from Global Constraints. Logo per spec: marigold rounded speech bubble, tail down-left, dark magnifier whose handle curves into a "J"; `viewBox="0 0 64 64"`, no text, no external refs. (The spec's PNG sizes 16/32/48/128 are made in sub-project B, the only place that uses them.)

- [ ] **Step 1: Write failing tests**: `test_static_css_served`, `test_no_blue_in_css` (no hex colour in `app.css` whose hue is in 180°–260° with saturation > 25%; parse with `colorsys`), `test_logo_and_favicon_served_as_svg`, `test_admin_link_only_for_owner`, `test_pages_use_base_layout` (each page contains `class="topbar"`).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**; check at 375px width in a browser screenshot (ask the user for one after deploy).
- [ ] **Step 4: Run** `pytest -q` → PASS.
- [ ] **Step 5: Commit** `feat(web): marigold look and logo`.

### Task 10: Rollout and docs

**Files:**
- Modify: `render.yaml` (`buildCommand: pip install -e ".[web]" && python -m jobhunter migrate`; env keys `OWNER_EMAIL`, `FIREBASE_*` as `sync: false`; drop `WEB_PASSWORD`), `README.md` (sign-in setup, owner claim, adding users, limits, email-for-all switch, changing owner manually), `CLAUDE.md` (settings are per user; every per-user query filters by `user_id`), `tests/test_phase0.py` if it checks env names
- Test: `tests/test_render_blueprint.py`

- [ ] **Step 1: Write failing test** `test_render_build_runs_migrations` (yaml `buildCommand` contains `python -m jobhunter migrate`) and `test_render_has_no_web_password`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** `ruff check` and `pytest -q` → PASS.
- [ ] **Step 5: Commit** `chore: render migrates on build; docs for accounts`.
- [ ] **Step 6: Hand over**: user pushes (`git push origin main`), waits for Render, signs in with the owner Google account first, checks My jobs and Admin, then removes `WEB_PASSWORD` from Render.
