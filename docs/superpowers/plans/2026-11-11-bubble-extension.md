# JobHunter Bubble Extension Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Manifest V3 Chrome extension that shows a floating JobHunter bubble on job pages: match check against the user's filters, Save / Mark applied, latest matches, already-seen badge; plus the server API and connect flow behind it.

**Architecture:** The FastAPI app gains revocable per-browser tokens (hashed in a new table), a small JSON API under `/api/v1` authenticated by `Authorization: Bearer`, and a `/extension/connect` page. The extension is plain JavaScript (no build step): classic content scripts (site readers + shadow-DOM bubble) talk by `chrome.runtime` messages to a module service worker, which alone holds the token and calls the API.

**Tech Stack:** Python (FastAPI, SQLAlchemy, Alembic, pydantic), Chrome MV3 JavaScript, Node 22 built-in test runner + `linkedom` (dev-only) for reader tests, headless Edge for icon PNGs.

**Spec:** `docs/superpowers/specs/2026-11-11-bubble-extension-design.md` (A's spec for palette/logo: `2026-11-11-accounts-and-new-look-design.md`)

## Global Constraints

- **Passive only:** content scripts read the open page's DOM; never click, scroll, open pages, submit forms, or request anything from the job sites. No polling faster than once a second, and only while `document.visibilityState === "visible"`.
- Permissions exactly: `"permissions": ["storage"]`; host permissions only the job sites below and `https://jobhunter-settings.onrender.com/*`. No `tabs`, `history`, `cookies`, `webRequest`, `<all_urls>`.
- Job sites (match patterns): `https://www.linkedin.com/jobs/*`, `https://www.naukri.com/*`, `https://in.indeed.com/*`, `https://www.indeed.com/*`, `https://boards.greenhouse.io/*`, `https://job-boards.greenhouse.io/*`, `https://jobs.lever.co/*`, `https://jobs.ashbyhq.com/*`, `https://*.myworkdayjobs.com/*`, `https://jobs.smartrecruiters.com/*`.
- API: `/api/v1`, Bearer token, JSON; 401 bad/expired/blocked, 422 bad input, 429 over 300 requests per token per hour. Input limits: title/company/location ≤ 300 chars, description ≤ 20,000, URL `https` on a supported host.
- Tokens: 32 random bytes (`secrets.token_urlsafe(32)`), stored only as SHA-256 hex, never logged or shown after connect.
- Bubble: 48 px, bottom-right default, draggable, position per hostname; ring **green** `#4F8A4B` match, **grey** `#9A9086` no match, **marigold** `#E09F3E` outline checking/no job; seen = small dot. Panel 320 px. Shadow DOM. Copy: "Matches your filters", "Not a match: <reason>", "Open a job to check it", "Connect JobHunter", "Waking up JobHunter, this can take up to a minute…", "Save to JobHunter" → "Saved ✓", "Mark applied", "Your latest matches", "Open My jobs".
- No blue in extension CSS or icons (same rule as A).
- Server URL lives in one place: `extension/config.js` (`JOBHUNTER_URL`).
- `ruff check`, `pytest`, and `npm test` (in `extension/`) pass at the end of every task that touches them.

## Review Focus

1. **Server asleep / slow (Render free tier):** the bubble shows the waking message after 3 s, the worker retries once, and after the retry fails the panel says "JobHunter didn't answer, try again" — never an endless spinner. → Task 5 test `test_slow_server_retries_once_then_gives_up`.
2. **Token revoked or user blocked while connected:** API 401 → worker deletes its token and the bubble shows "Connect JobHunter". → Task 5 test `test_401_forgets_token`.
3. **Job switches without a page load (LinkedIn/Indeed panes):** a result for the previous job arriving late must not be shown for the new one. → Task 7 test `test_stale_result_ignored`.
4. **Pages that are not a job** (feed, search list, company page): no API call; bubble shows "Open a job to check it". → Task 6 tests `test_linkedin_feed_is_not_a_job`, `test_naukri_search_is_not_a_job`.
5. **Oversized or hostile page data:** description over 20,000 chars is trimmed by the reader; a URL on an unsupported host is refused by the server with 422; HTML in titles is shown as text. → Task 2 tests `test_check_rejects_foreign_url`, `test_check_rejects_long_title`; Task 7 test `test_panel_shows_html_as_text`.

---

## File map

| File | Responsibility |
|---|---|
| `alembic/versions/0006_extension_tokens.py`, `src/jobhunter/db.py` | `extension_tokens` table / `ExtensionToken` model |
| `src/jobhunter/extension_tokens.py` | create, find user by token, list, revoke |
| `src/jobhunter/web/api.py` | `/api/v1` router, `JobInfo` model, rate limiter, `DbApi` gateway |
| `src/jobhunter/web/app.py`, `templates/extension_connect.html`, `templates/settings.html` | connect page, Extension card, wiring |
| `extension/manifest.json`, `config.js`, `background.js`, `api.js` | MV3 manifest, server URL, worker, testable API client |
| `extension/readers/*.js` | `common.js` (namespace, trimming), `jsonld.js`, `linkedin.js`, `naukri.js`, `indeed.js`, `index.js` (pick reader by host) |
| `extension/bubble/view.js`, `bubble/bubble.js`, `bubble/bubble.css` | pure view model; shadow-DOM UI; styles |
| `extension/content.js`, `connect.js` | page watcher; connect-page handoff |
| `extension/popup.html`, `popup.js` | connected status, auto-check toggle, hidden sites |
| `extension/icons/*.png`, `extension/tools/make_icons.py` | icons 16/32/48/128 from A's logo |
| `extension/package.json`, `extension/test/*.test.js`, `extension/test/fixtures/*.html` | Node tests and cleaned page samples |

---

### Task 1: Extension tokens (table + module)

**Files:** Create `alembic/versions/0006_extension_tokens.py`, `src/jobhunter/extension_tokens.py`, `tests/test_extension_tokens.py`; modify `src/jobhunter/db.py`, `tests/test_phase0.py` (table list).

**Interfaces:**
- Produces: `ExtensionToken(id int PK, user_id FK users cascade, token_hash text unique, label text, created_at, last_used_at)`.
- `create_token(session, user_id: int, label: str) -> str` (raw token; label trimmed to 60 chars)
- `user_for_token(session, raw: str, now: datetime) -> int | None` (None if unknown or user blocked; sets `last_used_at`; constant-time-safe because lookup is by hash)
- `list_tokens(session, user_id) -> list[TokenRow]` where `TokenRow(id, label, created_at, last_used_at)`
- `revoke_token(session, user_id, token_id) -> bool` (False if not that user's)
- `label_from_user_agent(ua: str) -> str` → e.g. `"Chrome on Windows"`, `"Edge on macOS"`, fallback `"Browser"`.

- [ ] **Step 1: Failing tests** (DB): `test_token_round_trip` (create → user_for_token returns user, `last_used_at` set), `test_only_hash_stored` (raw token not in any column), `test_unknown_token_none`, `test_blocked_user_token_refused`, `test_revoke_only_own`, `test_list_tokens_per_user`; pure: `test_label_from_user_agent` (Chrome/Windows, Edge/macOS — Edge UA contains "Edg/", Android Chrome → "Chrome on Android", empty → "Browser"); migration: extend `tests/test_migration_users.py::test_models_match_migration` (already compares all tables — it fails until 0006 exists).
- [ ] **Step 2:** `pytest tests/test_extension_tokens.py tests/test_migration_users.py -q` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** `pytest -q` → PASS. **Step 5: Commit** `feat: per-browser extension tokens`.

### Task 2: API for the extension

**Files:** Create `src/jobhunter/web/api.py`, `tests/test_api.py` (fake gateway), `tests/test_api_db.py` (DbApi on Postgres); modify `src/jobhunter/web/app.py` (include router; `api` param).

**Interfaces:**
- Consumes: Task 1 `user_for_token`; A's `normalize_job`, `best_result`, `load_active_searches`, `upsert_jobs(..., private_to)`, `visible_to`, `JobsStore.list/set_status`.
- Produces:
  - `class JobInfo(BaseModel)`: `url: str`, `title: str (1..300)`, `company: str (1..300)`, `location: str | None (≤300)`, `description: str | None (≤20000)`; validator: `https` and host matches `SUPPORTED_HOSTS` (exact hosts above, plus suffix `.myworkdayjobs.com`).
  - `class SaveRequest(JobInfo)`: `status: Literal["saved", "applied"]`.
  - `@dataclass CheckResult(verdict: Literal["match","no_match"], reasons: list[str], seen: Seen)`; `Seen(status: str | None, emailed: bool, listed: bool)`.
  - `class ApiGateway(Protocol)`: `user_for_token(raw) -> int | None`, `check(user_id, info) -> CheckResult`, `save(user_id, req) -> int` (job id), `matches(user_id, limit) -> list[JobCard]`, `me(user_id) -> dict`.
  - `class DbApi(session_factory)`: check = `normalize_job(RawJob(source="extension", ...))`, `best_result` over the user's searches (no searches → `no_match`, reason `"Add job titles on Settings first"`), seen from the stored job with the same `dedupe_key` visible to the user (status, `emailed_at is not None`, `listed` = passed score row); save = `upsert_jobs([...], now, private_to=user_id)` then `set_status`.
  - `class RateLimiter(limit=300, window=3600, clock=time.monotonic)`: `.allow(key) -> bool` (sliding window per key).
  - `api_router(gateway: ApiGateway, limiter: RateLimiter) -> APIRouter` with `POST /api/v1/check`, `POST /api/v1/jobs`, `GET /api/v1/matches?limit=1..20 (default 5)`, `GET /api/v1/me`. Rate limit keyed by token hash.
  - `create_app(..., api: ApiGateway | None = None)` includes the router; responses carry `Cache-Control: no-store`.

- [ ] **Step 1: Failing tests** (fake gateway): `test_check_needs_bearer` (no header / wrong scheme / unknown token → 401), `test_check_match`, `test_check_no_match_reason`, `test_check_rejects_foreign_url` (422), `test_check_rejects_long_title` (422), `test_save_sets_status`, `test_save_rejects_bad_status`, `test_matches_limit_bounds` (0 and 21 → 422), `test_rate_limited_after_300` (limiter with limit=2), `test_api_ignores_session_cookie` (signed-in browser without bearer → 401), `test_api_no_store`. DB: `test_dbapi_check_uses_users_filters`, `test_dbapi_seen_after_save`, `test_dbapi_saved_job_private_to_user`, `test_dbapi_no_titles_reason`, `test_dbapi_matches_are_users_own`.
- [ ] **Step 2:** run → FAIL. **Step 3: Implement.** **Step 4:** `pytest -q` → PASS.
- [ ] **Step 5: Commit** `feat(api): check, save and matches for the extension`.

### Task 3: Connect page and Extension card

**Files:** Create `templates/extension_connect.html`, `tests/test_web_extension.py`; modify `app.py`, `templates/settings.html`, `web/store.py` (`UserStore.tokens()`, `UserStore.revoke_token(id)`, `UserStore.create_token(label)`).

**Interfaces:**
- `GET /extension/connect` (signed in): page with **Connect this browser** button (POST form, CSRF).
- `POST /extension/connect`: creates a token with `label_from_user_agent(User-Agent)` and renders the page with `<div id="jh-connect" data-token="RAW" hidden></div>` and text "Connecting…"; the content script (Task 8) replaces it with "Connected ✓". Response headers add `Cache-Control: no-store` and `Referrer-Policy: no-referrer`.
- `POST /extension/tokens/{id}/delete` (CSRF) → 303 `/settings#extension`; 404 if not the user's.
- Settings card `id="extension"`: "Connect extension" link to `/extension/connect`, list of browsers (label, connected date, last used) with **Disconnect**.

- [ ] **Step 1: Failing tests:** `test_connect_page_needs_sign_in`, `test_connect_creates_token_once_per_post` (GET never creates), `test_connect_needs_csrf`, `test_connect_page_not_cached`, `test_settings_lists_connected_browsers`, `test_disconnect_only_own_token` (404 for another user's id), `test_token_not_shown_on_settings`.
- [ ] **Step 2–4:** RED → implement → `pytest -q` PASS. **Step 5: Commit** `feat(web): connect the extension from Settings`.

### Task 4: Extension skeleton, icons and test setup

**Files:** Create `extension/manifest.json`, `config.js`, `package.json` (no `"type"` field: readers are classic scripts loaded with `require`, while `api.js` uses `export` and Node 22's automatic module detection loads it with `import()`; `"engines": {"node": ">=22.7"}`, `"scripts": {"test": "node --test test/"}`, devDependency `linkedom` pinned `0.18.5`), `extension/tools/make_icons.py`, `extension/icons/icon{16,32,48,128}.png`, `extension/test/manifest.test.js`, `extension/README.md`; modify `.gitignore` (`extension/node_modules/`, `extension/test/fixtures/raw/`).

**Interfaces:**
- `manifest.json`: `manifest_version: 3`, `name: "JobHunter bubble"`, `version: "0.1.0"`, `permissions`/`host_permissions` per Global Constraints, `background.service_worker: "background.js"`, `"type": "module"`, two `content_scripts` entries (job sites → readers…, bubble…, `content.js`; connect page `https://jobhunter-settings.onrender.com/extension/connect*` → `connect.js`), `action.default_popup: "popup.html"`, `icons`.
- `make_icons.py`: renders `src/jobhunter/web/static/logo.svg` with headless Edge (`--default-background-color=00000000`) at 16/32/48/128 px into `extension/icons/`.

- [ ] **Step 1: Failing tests** `extension/test/manifest.test.js`: `permissions are exactly storage`, `no broad host permissions` (no `<all_urls>`, no `*://*/*`), `every content script file exists`, `icons exist at all four sizes`, `server url only in config.js` (grep other `.js` files for `onrender.com` → none).
- [ ] **Step 2:** user runs `cd extension; npm install` once (installs `linkedom`); `npm test` → FAIL.
- [ ] **Step 3: Implement**; run `make_icons.py`; view the 16/48 px PNGs.
- [ ] **Step 4:** `npm test` → PASS. **Step 5: Commit** `feat(extension): manifest, icons and test setup`.

### Task 5: Service worker and API client

**Files:** Create `extension/api.js` (ES module), `extension/background.js`, `extension/test/api.test.js`.

**Interfaces:**
- `api.js` exports `makeClient({ fetch, storage, baseUrl, sleep, timeoutMs = 70000 })` returning `{ check(job), save(job, status), matches(limit = 5), me(), connect(token), disconnect(), isConnected() }`. Each call resolves to `{ ok: true, data }` or `{ ok: false, error: "not_connected" | "waking_failed" | "bad_input" | "rate_limited" | "server" }`. Storage: `{ get(key), set(key, value), remove(key) }` (async; background wires `chrome.storage.local`). Network error or timeout → one retry after `sleep(5000)`; second failure → `waking_failed`. 401 → `storage.remove("token")` + `not_connected`.
- `background.js`: `chrome.runtime.onMessage` handler for `{type: "check"|"save"|"matches"|"me"|"connect"|"status"}` → client call → `sendResponse` (returns `true` for async). `connect` accepted only from a sender whose `url` starts with `JOBHUNTER_URL + "/extension/connect"`.

- [ ] **Step 1: Failing tests** with a fake fetch/storage: `test_check_sends_bearer_and_json`, `test_not_connected_without_token` (no fetch made), `test_401_forgets_token`, `test_slow_server_retries_once_then_gives_up`, `test_retry_succeeds`, `test_429_is_rate_limited`, `test_422_is_bad_input`, `test_connect_stores_token`, `test_connect_rejects_empty_token`.
- [ ] **Step 2–4:** RED → implement → `npm test` PASS. **Step 5: Commit** `feat(extension): service worker and API client`.

### Task 6: Site readers

**Files:** Create `extension/readers/common.js`, `jsonld.js`, `linkedin.js`, `naukri.js`, `indeed.js`, `index.js`; `extension/test/readers.test.js`; fixtures `extension/test/fixtures/{careers_greenhouse,careers_workday,linkedin_job,linkedin_pane,linkedin_feed,naukri_job,naukri_search,indeed_job}.html`.

**Prerequisite (user):** while signed in as usual, open one job on each of LinkedIn (a `/jobs/view/` page, and a search page with a job selected in the right pane), Naukri and Indeed, and save each with **Ctrl+S → "Webpage, HTML only"** into `extension/test/fixtures/raw/` (git-ignored). The implementer cuts each down to the job area, removes the user's name, photo, email, IDs and tracking parameters, and saves the cleaned file in `fixtures/`. Careers fixtures are written from the schema.org `JobPosting` format (no download needed).

**Interfaces:**
- All readers attach to `globalThis.JH` (classic scripts; Node tests `require` them after setting `globalThis.JH = {}`).
- `JH.JobInfo` shape: `{ url, title, company, location, description }` (strings or `null`; `description` trimmed to 20,000 chars by `JH.trim`).
- `JH.readJsonLd(document, location) -> JobInfo | null` (first `JobPosting` in any `application/ld+json`, including inside `@graph` arrays; location from `jobLocation.address` locality/region/country, or `"Remote"` when `jobLocationType == "TELECOMMUTE"`; description text with tags stripped).
- `JH.readLinkedIn(document, location)`: job id from `/jobs/view/<id>` or `currentJobId=<id>`; URL `https://www.linkedin.com/jobs/view/<id>/`; title/company/location from the visible job details pane (selectors chosen from the real fixtures, each with one fallback); `null` without a job id.
- `JH.readNaukri(document, location)`: only on `/job-listings-` URLs; JSON-LD first, else page elements; URL without query.
- `JH.readIndeed(document, location)`: `vjk=` / `jk=` id or `/viewjob`; JSON-LD first, else job pane; URL `https://<host>/viewjob?jk=<id>`.
- `JH.readJob(document, location) -> JobInfo | null`: picks by host (LinkedIn, Naukri, Indeed, else JSON-LD for careers hosts).

- [ ] **Step 1: Failing tests**: `careers greenhouse from JSON-LD`, `careers workday with @graph`, `remote JSON-LD location`, `description trimmed to 20000`, `linkedin job page`, `linkedin right pane uses currentJobId`, `test_linkedin_feed_is_not_a_job`, `naukri job page`, `test_naukri_search_is_not_a_job`, `indeed job`, `readJob picks by host`, `fixtures contain no personal data` (each cleaned fixture has no `@gmail.com`, no `miniprofile`, no `trackingId=`).
- [ ] **Step 2–4:** RED → implement → `npm test` PASS. **Step 5: Commit** `feat(extension): readers for LinkedIn, Naukri, Indeed and careers pages`.

### Task 7: Bubble, content script and popup

**Files:** Create `extension/bubble/view.js`, `bubble/bubble.js`, `bubble/bubble.css`, `extension/content.js`, `extension/popup.html`, `extension/popup.js`, `extension/test/view.test.js`, `extension/test/content.test.js`.

**Interfaces:**
- `JH.viewModel(state) -> { ring: "match"|"nomatch"|"pending", dot: bool, headline, detail, actions: [...], showConnect: bool }` for states `{kind: "nojob"} | {kind: "checking", slow: bool} | {kind: "result", job, result} | {kind: "error", error}` — pure, all copy from Global Constraints.
- `JH.Bubble(root = document.body, storage)`: creates a shadow-root host `#jobhunter-bubble-host`, `render(viewModel)`, drag with pointer events (position saved per `location.hostname`), panel toggle, emits `save`, `applied`, `open-matches` events. All text set with `textContent`.
- `JH.Watcher({ readJob, send, bubble, now, visible })`: every 1 s while visible, if `location.href` changed, read the job; if none → `nojob` (no message sent); else send `check` with a request id and ignore any reply whose id is not the latest. Shows `slow: true` after 3 s. Honors `autoCheck` (popup toggle; default on) and the per-site hidden list.
- `popup.js`: shows connected / not connected (with **Connect** opening `JOBHUNTER_URL + "/extension/connect"` in a new tab via `window.open`), the auto-check toggle, and hidden sites with **Show again**.

- [ ] **Step 1: Failing tests**: view model for each state (match → green ring + "Matches your filters"; no match → "Not a match: needs 5+ years"; seen → dot; not connected → showConnect; slow → waking copy; error `waking_failed` → "JobHunter didn't answer, try again"); `test_panel_shows_html_as_text` (title `<b>x</b>` rendered as text, using linkedom); watcher: `test_no_job_sends_nothing`, `test_stale_result_ignored`, `test_url_change_rechecks`, `test_hidden_tab_does_not_poll`, `test_auto_check_off_waits_for_click`.
- [ ] **Step 2–4:** RED → implement → `npm test` PASS. **Step 5: Commit** `feat(extension): floating bubble, page watcher and popup`.

### Task 8: Connect handoff, docs and hand-over

**Files:** Create `extension/connect.js`, `extension/test/connect.test.js`; modify `CLAUDE.md` (rule: the extension may read the job page the user has open in their own browser; it never clicks, scrolls, navigates, polls faster than 1 s or calls LinkedIn/Naukri/Indeed), `README.md` (install "Load unpacked", connect, privacy, what it never does), `extension/README.md`.

**Interfaces:**
- `connect.js`: on the connect page, if `#jh-connect[data-token]` exists, sends `{type: "connect", token}` once, removes the attribute, and sets the page text to "Connected ✓ You can close this tab." or "Couldn't connect, reload this page."

- [ ] **Step 1: Failing tests**: `sends token once and clears it`, `no element → nothing sent`, `shows failure text`.
- [ ] **Step 2–4:** RED → implement → `npm test`, `ruff check`, `pytest -q` PASS. **Step 5: Commit** `feat(extension): connect handoff; docs`.
- [ ] **Step 6: Manual checklist with the user** after push and "Load unpacked": on each site open a job → ring colour; Save → appears in My jobs Saved; latest matches list; seen dot on a saved job; hide on one site and undo in popup; disconnect in Settings → bubble asks to connect; first click after the server slept shows the waking message.
