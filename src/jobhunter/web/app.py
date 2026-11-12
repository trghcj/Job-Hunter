"""JobHunter web app: Google sign-in (Firebase), then each user's own pages.

Start with:  uvicorn jobhunter.web.app:create_app --factory
Needs SESSION_SECRET (32+ random characters) and the four FIREBASE_* values.
"""

import hmac
import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware

from jobhunter.accounts import Access, Identity, SignIn, email_set
from jobhunter.actions import ACTIONS, Action, TokenInvalid, action_key, read_token
from jobhunter.avoid import REASONS
from jobhunter.drafts import FAILED, DraftError, JobNotFound
from jobhunter.extension_tokens import label_from_user_agent
from jobhunter.llm.client import LLMClient
from jobhunter.notify.email_resend import IST
from jobhunter.profile import MAX_PDF_BYTES, ProfileError
from jobhunter.settings import Settings, get_settings
from jobhunter.sources.catalog import SOURCE_LABELS
from jobhunter.sources.watchlist import format_watchlist
from jobhunter.web.actions_store import DbActions, JobBrief
from jobhunter.web.api import ApiGateway, DbApi, RateLimiter, api_router
from jobhunter.web.firebase_auth import FirebaseVerifier, TokenError
from jobhunter.web.forms import WORK_MODES, parse_admin_form, parse_form, parse_gmail_form
from jobhunter.web.jobs_store import FIT_FILTERS, TABS, JobsStore, JobsView
from jobhunter.web.store import AdminStore, SettingsStore, UserStore

log = logging.getLogger(__name__)

HERE = Path(__file__).parent
TEMPLATES = Jinja2Templates(directory=str(HERE / "templates"))
TEMPLATES.env.filters["safe_url"] = lambda url: (
    url if isinstance(url, str) and url.startswith(("https://", "http://")) else "")


def ist(value: datetime | None, fmt: str, missing: str = "") -> str:
    """A database time (UTC) as India time for display; `missing` when there is none."""
    if value is None:
        return missing
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(IST).strftime(fmt)


TEMPLATES.env.filters["ist"] = ist


def days_ago(value: datetime | None, now: datetime | None = None) -> str:
    """"today", "yesterday" or "N days ago", counted in India dates."""
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    days = ((now or datetime.now(UTC)).astimezone(IST).date() - value.astimezone(IST).date()).days
    return "today" if days <= 0 else "yesterday" if days == 1 else f"{days} days ago"


TEMPLATES.env.filters["days_ago"] = days_ago


def greeting(now: datetime, name: str | None) -> str:
    """"Good morning, Asha" by India time; "Hello" late at night; no name if unknown."""
    hour = now.astimezone(IST).hour
    if 5 <= hour < 12:
        hello = "Good morning"
    elif 12 <= hour < 17:
        hello = "Good afternoon"
    elif 17 <= hour < 23:
        hello = "Good evening"
    else:
        hello = "Hello"
    words = (name or "").split()
    return f"{hello}, {words[0]}" if words else hello
SESSION_DAYS = 30
AVOID_GROUPS = (("companies", "Hidden companies"), ("cities", "Hidden cities"),
                ("skills", "Lowered skills"), ("titles", "Lowered roles"))
LINK_EXPIRED = "This link has expired, open My jobs instead."
ACTION_DONE = {"save": "Saved ✓", "applied": "Marked as applied ✓",
               "not_interested": "Hidden ✓"}
FIT_CHOICES = (("strong", "Strong fits only"), ("good", "Good and strong"),
               ("all", "Everything that passed"))
PROFILE_NOTICES = {
    "saved": "Profile saved. Your matches are ranked by it now.",
    "options": "Choices saved.",
}
# Upload size check before the form is parsed: the PDF limit plus room for the other fields.
MAX_UPLOAD_REQUEST = MAX_PDF_BYTES + 64_000
GMAIL_NOTICES = {
    "saved": "Gmail saved. Use Test connection to check it.",
    "removed": "Gmail account removed.",
    "digest": "Saved. Your daily digest will come from your Gmail if that box is ticked.",
}
BASE_CSP = ("default-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https://lh3.googleusercontent.com; "
            "form-action 'self'; frame-ancestors 'none'")
SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
}
FIREBASE_FIELDS = ("firebase_api_key", "firebase_auth_domain", "firebase_project_id",
                   "firebase_app_id")


@dataclass(frozen=True)
class UserInfo:
    id: int
    email: str | None
    name: str | None
    is_owner: bool
    blocked: bool
    is_admin: bool = False  # the owner, or listed in ADMIN_EMAILS
    invited: bool = True  # False once removed from ALLOWED_EMAILS


class AccountsGateway(Protocol):
    def sign_in(self, identity: Identity) -> SignIn: ...
    def get_user(self, user_id: int) -> UserInfo | None: ...


class AdminView(Protocol):
    def load_sources(self) -> dict[str, bool]: ...
    def load_watchlist(self) -> list: ...
    def save_sources_and_watchlist(self, enabled: dict[str, bool], watchlist: list) -> None: ...
    def users(self) -> list: ...
    def set_blocked(self, user_id: int, blocked: bool) -> None: ...


class ActionGateway(Protocol):
    def apply(self, user_id: int, job_id: int, status: str) -> JobBrief | None: ...
    def reasons_for(self, user_id: int, job_id: int) -> list[str]: ...
    def add_reason(self, user_id: int, job_id: int, reason: str) -> bool: ...


class TokenVerifier(Protocol):
    def verify(self, token: str) -> Identity: ...


class DbAccounts:
    """AccountsGateway backed by PostgreSQL (jobhunter.accounts)."""

    def __init__(self, session_factory: sessionmaker[Session], access: Access) -> None:
        self._sessions = session_factory
        self._access = access

    def sign_in(self, identity: Identity) -> SignIn:
        from jobhunter import accounts

        with self._sessions() as session:
            return accounts.sign_in(session, identity, self._access.owner_email,
                                    datetime.now(UTC), access=self._access)

    def get_user(self, user_id: int) -> UserInfo | None:
        from jobhunter import accounts

        with self._sessions() as session:
            u = accounts.get_user(session, user_id)
            if u is None:
                return None
            return UserInfo(u.id, u.email, u.name, u.is_owner, u.blocked,
                            is_admin=self._access.is_admin(u.email, u.is_owner),
                            invited=self._access.invited(u.email, u.is_owner))


def _csrf(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = request.session["csrf"] = secrets.token_urlsafe(32)
    return token


def _csrf_ok(request: Request, submitted: object) -> bool:
    expected = request.session.get("csrf")
    return bool(expected and isinstance(submitted, str) and submitted
                and hmac.compare_digest(expected, submitted))


def _see_other(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def _login_csp(auth_domain: str) -> str:
    frames = f"https://{auth_domain} https://accounts.google.com" if auth_domain else \
        "https://accounts.google.com"
    return (BASE_CSP + "; script-src 'self' https://www.gstatic.com https://apis.google.com"
            "; connect-src 'self' https://identitytoolkit.googleapis.com "
            "https://securetoken.googleapis.com https://www.googleapis.com"
            f"; frame-src {frames}")


def create_app(
    store_factory: Callable[[int], SettingsStore] | None = None,
    jobs_store: JobsView | None = None,
    admin_store: AdminView | None = None,
    api: ApiGateway | None = None,
    api_limiter: RateLimiter | None = None,
    verifier: TokenVerifier | None = None,
    accounts: AccountsGateway | None = None,
    settings: Settings | None = None,
    actions: ActionGateway | None = None,
    draft_client: Callable[[], LLMClient | None] | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    if draft_client is None:
        from jobhunter.llm.gemini import client_from_settings

        shared = client_from_settings(settings)  # one HTTP client for every draft

        def draft_client() -> LLMClient | None:
            return shared
    secret = settings.session_secret.get_secret_value()
    if len(secret) < 32:
        raise RuntimeError("Set SESSION_SECRET (32+ random characters) to start.")
    missing = [f.upper() for f in FIREBASE_FIELDS if not getattr(settings, f)]

    if None in (store_factory, accounts, jobs_store, admin_store, api, actions):
        from jobhunter.db import make_engine

        sessions = sessionmaker(make_engine())
        key = settings.settings_key.get_secret_value()
        store_factory = store_factory or (lambda uid: UserStore(sessions, uid, key))
        access = Access(settings.owner_email, email_set(settings.allowed_emails),
                        email_set(settings.admin_emails))
        accounts = accounts or DbAccounts(sessions, access)
        jobs_store = jobs_store or JobsStore(sessions)
        admin_store = admin_store or AdminStore(sessions)
        api = api or DbApi(sessions, access)
        actions = actions or DbActions(sessions, access)
    if verifier is None and not missing:
        verifier = FirebaseVerifier(settings.firebase_project_id)

    app = FastAPI(title="JobHunter", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.draft_client = draft_client
    app.add_middleware(
        SessionMiddleware,
        secret_key=secret,
        https_only=settings.web_https_only,
        same_site="lax",
        max_age=SESSION_DAYS * 86400,
        session_cookie="jobhunter_session",
    )
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
    app.include_router(api_router(api, api_limiter or RateLimiter()))
    login_csp = _login_csp(settings.firebase_auth_domain)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response: Response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        if request.url.path.startswith(("/extension/connect", "/a/")):
            response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            login_csp if request.url.path == "/login" else BASE_CSP)
        return response

    def current_user(request: Request) -> UserInfo | None:
        uid = request.session.get("uid")
        if not isinstance(uid, int):
            return None
        user = accounts.get_user(uid)
        if user is None:
            request.session.clear()
        return user

    def signed_in(request: Request) -> tuple[UserInfo | None, Response | None]:
        """The signed-in user, or the redirect to send instead."""
        user = current_user(request)
        if user is None:
            return None, _see_other("/login")
        if user.blocked:
            request.session.clear()
            return None, _see_other("/blocked")
        if not user.invited:
            request.session.clear()
            return None, _see_other("/invite-only")
        return user, None

    # ---- one-click links from the daily email (no sign-in; the signed token authorises)

    settings_key = settings.settings_key.get_secret_value()
    link_key = action_key(settings_key) if settings_key else None

    def action_page(request: Request, mode: str, status: int = 200, **ctx):
        response = TEMPLATES.TemplateResponse(request, "action.html", {"mode": mode, **ctx},
                                              status_code=status)
        response.headers["Cache-Control"] = "no-store"
        return response

    def read_link(token: str) -> Action | None:
        if link_key is None:
            return None
        try:
            return read_token(link_key, token)
        except TokenInvalid:
            return None

    def expired(request: Request):
        return action_page(request, "expired", status=400, heading="Link expired",
                           text=LINK_EXPIRED)

    @app.get("/a/{token}", response_class=HTMLResponse)
    def action_confirm(request: Request, token: str):
        if read_link(token) is None:
            return expired(request)
        return action_page(request, "confirm", heading="One moment", token=token)

    @app.post("/a/{token}")
    async def action_apply(request: Request, token: str):
        link = read_link(token)
        form = await request.form()
        if link is None or form.get("confirm") != "1":
            return expired(request)
        job = actions.apply(link.user_id, link.job_id, ACTIONS[link.action])
        if job is None:
            return expired(request)
        reasons = []
        if link.action == "not_interested":
            reasons = [(r, REASONS[r]) for r in actions.reasons_for(link.user_id, link.job_id)]
        return action_page(request, "done", heading=ACTION_DONE[link.action], job=job,
                           reason_url=f"/a/{token}/reason", reasons=reasons)

    @app.post("/a/{token}/reason")
    async def action_reason(request: Request, token: str):
        link = read_link(token)
        form = await request.form()
        reason = str(form.get("reason", ""))
        if link is None or link.action != "not_interested" or reason not in REASONS:
            return expired(request)
        actions.add_reason(link.user_id, link.job_id, reason)
        return action_page(request, "reason", heading="Got it")

    @app.get("/healthz")
    def healthz() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request):
        user = current_user(request)
        if user is not None and not user.blocked:
            return _see_other("/")
        if missing:
            log.error("Sign-in is not set up; missing: %s", ", ".join(missing))
        ctx = {"csrf": _csrf(request), "configured": not missing, "fb": settings}
        return TEMPLATES.TemplateResponse(request, "login.html", ctx)

    @app.post("/auth/session")
    async def auth_session(request: Request):
        try:
            data = await request.json()
        except ValueError:
            return JSONResponse({"error": "Bad request."}, status_code=400)
        if not isinstance(data, dict):
            return JSONResponse({"error": "Bad request."}, status_code=400)
        if not _csrf_ok(request, data.get("csrf")):
            return JSONResponse({"error": "Page expired, reload it."}, status_code=403)
        token = data.get("idToken")
        if verifier is None or not isinstance(token, str):
            return JSONResponse({"error": "Sign-in failed, please try again."}, status_code=401)
        try:
            identity = verifier.verify(token)
        except TokenError as exc:
            return JSONResponse({"error": str(exc)}, status_code=401)
        result = accounts.sign_in(identity)
        request.session.clear()
        if result.outcome != "ok" or result.user_id is None:
            return JSONResponse({"next": f"/{result.outcome}"})
        request.session["uid"] = result.user_id
        _csrf(request)
        return JSONResponse({"next": "/"})

    @app.post("/logout")
    async def logout(request: Request):
        form = await request.form()
        if _csrf_ok(request, form.get("csrf")):
            request.session.clear()
        return _see_other("/login")

    def notice(request: Request, title: str, text: str):
        ctx = {"title": title, "text": text}
        return TEMPLATES.TemplateResponse(request, "notice.html", ctx)

    @app.get("/blocked", response_class=HTMLResponse)
    def blocked_page(request: Request):
        return notice(request, "Your account is blocked",
                      "The owner of this JobHunter has blocked your account.")

    @app.get("/invite-only", response_class=HTMLResponse)
    def invite_only_page(request: Request):
        return notice(request, "JobHunter is invite-only for now",
                      "Ask the owner to add your Google account, then sign in again.")

    @app.get("/full", response_class=HTMLResponse)
    def full_page(request: Request):
        return notice(request, "JobHunter is full right now",
                      "It has reached its user limit. Please try again later.")

    @app.get("/", response_class=HTMLResponse)
    def my_jobs(request: Request):
        user, early = signed_in(request)
        if early:
            return early
        tab = request.query_params.get("tab", "matches")
        tab = tab if tab in TABS else "matches"
        fit = request.query_params.get("fit")
        fit = fit if tab == "matches" and fit in FIT_FILTERS else None
        ctx = {"csrf": _csrf(request), "user": user, "page": "jobs", "tab": tab, "tabs": TABS,
               "fit": fit, "counts": jobs_store.counts(user.id),
               "hello": greeting(datetime.now(UTC), user.name),
               "new_today": jobs_store.new_today(user.id),
               "cards": jobs_store.list(user.id, tab, fit=fit),
               "has_profile": bool(store_factory(user.id).load_profile().text)}
        return TEMPLATES.TemplateResponse(request, "jobs.html", ctx)

    @app.post("/jobs/{job_id}/status")
    async def set_job_status(request: Request, job_id: int):
        user, data, early = await _checked_form(request)
        if early:
            return early
        if not jobs_store.set_status(user.id, job_id, str(data.get("status", ""))):
            return Response("Job not found.", status_code=404)
        tab = str(data.get("tab", "matches"))
        return _see_other(f"/?tab={tab if tab in TABS else 'matches'}")

    # ---- "Not interested" on My jobs, with the same optional reasons as the email

    @app.post("/jobs/{job_id}/not-interested")
    async def not_interested(request: Request, job_id: int):
        user, _, early = await _checked_form(request)
        if early:
            return early
        if not jobs_store.set_status(user.id, job_id, "hidden"):
            return Response("Job not found.", status_code=404)
        job, reasons = store_factory(user.id).reasons_for(job_id)
        if job is None:
            return Response("Job not found.", status_code=404)
        return TEMPLATES.TemplateResponse(request, "action.html", {
            "mode": "done", "heading": ACTION_DONE["not_interested"], "job": job,
            "user": user, "csrf": _csrf(request), "reason_url": f"/jobs/{job_id}/reason",
            "reasons": [(r, REASONS[r]) for r in reasons]})

    @app.post("/jobs/{job_id}/reason")
    async def job_reason(request: Request, job_id: int):
        user, data, early = await _checked_form(request)
        if early:
            return early
        store_factory(user.id).add_reason(job_id, str(data.get("reason", "")))
        return TEMPLATES.TemplateResponse(request, "action.html", {
            "mode": "reason", "heading": "Got it", "user": user})

    # ---- cover letter and resume tips for one job (drafts.py)

    def draft_page(request: Request, user: UserInfo, job_id: int, error: str | None = None):
        view = store_factory(user.id).load_draft(job_id)
        if view is None:
            return Response("Job not found.", status_code=404)
        return TEMPLATES.TemplateResponse(request, "draft.html", {
            "user": user, "csrf": _csrf(request), "page": "jobs", "job_id": job_id,
            "view": view, "error": error})

    @app.get("/jobs/{job_id}/draft", response_class=HTMLResponse)
    def draft_get(request: Request, job_id: int):
        user, early = signed_in(request)
        if early:
            return early
        return draft_page(request, user, job_id)

    @app.post("/jobs/{job_id}/draft")
    async def draft_write(request: Request, job_id: int):
        user, _, early = await _checked_form(request)
        if early:
            return early
        client = draft_client()
        if client is None:
            return draft_page(request, user, job_id, FAILED)
        try:
            # The AI call can take up to a minute: keep it off the event loop.
            await run_in_threadpool(store_factory(user.id).make_draft, job_id, client)
        except JobNotFound:
            return Response("Job not found.", status_code=404)
        except DraftError as exc:
            return draft_page(request, user, job_id, str(exc))
        return _see_other(f"/jobs/{job_id}/draft")

    @app.post("/jobs/{job_id}/draft/letter")
    async def draft_letter(request: Request, job_id: int):
        user, data, early = await _checked_form(request)
        if early:
            return early
        if not store_factory(user.id).save_letter(job_id, str(data.get("letter", ""))):
            return Response("Draft not found.", status_code=404)
        return _see_other(f"/jobs/{job_id}/draft")

    @app.post("/avoid/remove")
    async def remove_avoid(request: Request):
        user, data, early = await _checked_form(request)
        if early:
            return early
        store_factory(user.id).remove_avoid(str(data.get("kind", "")),
                                            str(data.get("value", "")))
        return _see_other("/settings#avoid")

    # ---- settings (per user)

    def render_settings(request: Request, user: UserInfo, store: SettingsStore, form,
                        errors=(), saved=False, status=200, gmail_errors=(),
                        gmail_message: str | None = None, profile_errors=(),
                        profile_message: str | None = None):
        ctx = {
            "csrf": _csrf(request),
            "user": user,
            "page": "settings",
            "form": form,
            "errors": list(errors),
            "saved": saved,
            "modes": WORK_MODES,
            "mail": store.load_mail(),
            "gmail_errors": list(gmail_errors),
            "gmail_message": gmail_message,
            "tokens": store.tokens(),
            "runs": store.recent_runs(),
            "profile": store.load_profile(),
            "profile_errors": list(profile_errors),
            "profile_message": profile_message,
            "fit_choices": FIT_CHOICES,
            "avoid": store.load_avoid(),
            "avoid_groups": AVOID_GROUPS,
        }
        return TEMPLATES.TemplateResponse(request, "settings.html", ctx, status_code=status)

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request):
        user, early = signed_in(request)
        if early:
            return early
        store = store_factory(user.id)
        saved = request.query_params.get("saved") == "1"
        message = GMAIL_NOTICES.get(request.query_params.get("gmail", ""))
        profile_message = PROFILE_NOTICES.get(request.query_params.get("profile", ""))
        return render_settings(request, user, store, store.load(), saved=saved,
                               gmail_message=message, profile_message=profile_message)

    async def _checked_form(request: Request):
        """(user, posted form) if signed in with a valid CSRF token, else the response."""
        user, early = signed_in(request)
        if early:
            return None, None, early
        data = await request.form()
        if not _csrf_ok(request, data.get("csrf")):
            return None, None, Response("Form expired, reload the page.", status_code=403)
        return user, data, None

    @app.post("/settings")
    async def save_settings(request: Request):
        user, data, early = await _checked_form(request)
        if early:
            return early
        store = store_factory(user.id)
        fields = {k: str(v) for k, v in data.items() if isinstance(v, str)}
        modes = [str(m) for m in data.getlist("work_modes")]
        form, errors = parse_form(fields, modes)
        if errors:
            return render_settings(request, user, store, form, errors, status=400)
        store.save(form)
        return _see_other("/settings?saved=1")

    # ---- profile (resume)

    def profile_error(request: Request, user: UserInfo, store: SettingsStore, message: str):
        return render_settings(request, user, store, store.load(), profile_errors=[message],
                               status=400)

    @app.post("/profile")
    async def save_profile_text(request: Request):
        user, data, early = await _checked_form(request)
        if early:
            return early
        text = data.get("profile_text")
        store_factory(user.id).save_profile_text(text if isinstance(text, str) else "")
        return _see_other("/settings?profile=saved#profile")

    @app.post("/profile/resume")
    async def upload_resume(request: Request):
        size = request.headers.get("content-length", "")
        if size.isdigit() and int(size) > MAX_UPLOAD_REQUEST:
            user, early = signed_in(request)
            if early:
                return early
            return profile_error(request, user, store_factory(user.id),
                                 "Resume PDF must be 2 MB or smaller.")
        user, data, early = await _checked_form(request)
        if early:
            return early
        store = store_factory(user.id)
        upload = data.get("resume")
        if upload is None or isinstance(upload, str) or not upload.filename:
            return profile_error(request, user, store, "Choose a PDF file to upload.")
        content = await upload.read(MAX_PDF_BYTES + 1)
        if len(content) > MAX_PDF_BYTES:
            return profile_error(request, user, store, "Resume PDF must be 2 MB or smaller.")
        try:
            store.upload_resume(content)
        except ProfileError as exc:
            return profile_error(request, user, store, str(exc))
        return _see_other("/settings?profile=saved#profile")

    @app.post("/profile/options")
    async def save_profile_options(request: Request):
        user, data, early = await _checked_form(request)
        if early:
            return early
        store = store_factory(user.id)
        try:
            store.save_profile_options(data.get("llm_review") == "1",
                                       str(data.get("digest_fit", "")))
        except ValueError as exc:
            return profile_error(request, user, store, str(exc))
        return _see_other("/settings?profile=options#profile")

    @app.post("/gmail")
    async def save_gmail(request: Request):
        user, data, early = await _checked_form(request)
        if early:
            return early
        store = store_factory(user.id)
        fields = {k: str(v) for k, v in data.items() if isinstance(v, str)}
        address, app_password, senders, errors = parse_gmail_form(fields)
        if not errors:
            try:
                store.save_mail(address, app_password, senders)
            except ValueError as exc:
                errors = [str(exc)]
        if errors:
            return render_settings(request, user, store, store.load(), gmail_errors=errors,
                                   status=400)
        return _see_other("/settings?gmail=saved#gmail")

    @app.post("/gmail/test")
    async def test_gmail(request: Request):
        user, _, early = await _checked_form(request)
        if early:
            return early
        store = store_factory(user.id)
        return render_settings(request, user, store, store.load(),
                               gmail_message=store.test_mail())

    @app.post("/gmail/delete")
    async def delete_gmail(request: Request):
        user, _, early = await _checked_form(request)
        if early:
            return early
        store_factory(user.id).delete_mail()
        return _see_other("/settings?gmail=removed#gmail")

    @app.post("/gmail/digest")
    async def gmail_digest(request: Request):
        user, data, early = await _checked_form(request)
        if early:
            return early
        store_factory(user.id).set_send_digest(data.get("send_digest") == "1")
        return _see_other("/settings?gmail=digest#gmail")

    # ---- connecting the Chrome extension

    def connect_page(request: Request, user: UserInfo, token: str | None = None):
        ctx = {"csrf": _csrf(request), "user": user, "page": "settings", "token": token}
        response = TEMPLATES.TemplateResponse(request, "extension_connect.html", ctx)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/extension/connect", response_class=HTMLResponse)
    def extension_connect(request: Request):
        user, early = signed_in(request)
        return early or connect_page(request, user)

    @app.post("/extension/connect")
    async def extension_connect_post(request: Request):
        user, _, early = await _checked_form(request)
        if early:
            return early
        label = label_from_user_agent(request.headers.get("user-agent", ""))
        return connect_page(request, user, store_factory(user.id).create_token(label))

    @app.post("/extension/tokens/{token_id}/delete")
    async def extension_disconnect(request: Request, token_id: int):
        user, _, early = await _checked_form(request)
        if early:
            return early
        if not store_factory(user.id).revoke_token(token_id):
            return Response("Not found.", status_code=404)
        return _see_other("/settings#extension")

    # ---- admin (the owner and ADMIN_EMAILS; everyone else gets 404)

    def owner_only(request: Request) -> tuple[UserInfo | None, Response | None]:
        user, early = signed_in(request)
        if early:
            return None, early
        if not (user.is_owner or user.is_admin):
            return None, Response("Not found.", status_code=404)
        return user, None

    def render_admin(request: Request, user: UserInfo, errors=(), status=200,
                     sources: dict[str, bool] | None = None, watchlist_text: str | None = None,
                     saved=False):
        watchlist = admin_store.load_watchlist()
        ctx = {
            "csrf": _csrf(request), "user": user, "page": "admin", "errors": list(errors),
            "saved": saved,
            "users": admin_store.users(), "source_labels": SOURCE_LABELS,
            "sources": sources if sources is not None else admin_store.load_sources(),
            "watchlist_count": len(watchlist),
            "watchlist_text": (watchlist_text if watchlist_text is not None
                               else format_watchlist(watchlist)),
        }
        return TEMPLATES.TemplateResponse(request, "admin.html", ctx, status_code=status)

    @app.get("/admin", response_class=HTMLResponse)
    def admin_page(request: Request):
        user, early = owner_only(request)
        if early:
            return early
        return render_admin(request, user, saved=request.query_params.get("saved") == "1")

    async def _owner_form(request: Request):
        user, early = owner_only(request)
        if early:
            return None, None, early
        data = await request.form()
        if not _csrf_ok(request, data.get("csrf")):
            return None, None, Response("Form expired, reload the page.", status_code=403)
        return user, data, None

    @app.post("/admin/sources")
    async def save_admin_sources(request: Request):
        user, data, early = await _owner_form(request)
        if early:
            return early
        fields = {k: str(v) for k, v in data.items() if isinstance(v, str)}
        ticked = [str(x) for x in data.getlist("sources")]
        enabled, watchlist, errors = parse_admin_form(fields, ticked)
        if errors:
            return render_admin(request, user, errors, status=400, sources=enabled,
                                watchlist_text=fields.get("watchlist", ""))
        admin_store.save_sources_and_watchlist(enabled, watchlist)
        return _see_other("/admin?saved=1")

    @app.post("/admin/users/{user_id}/block")
    async def block_user(request: Request, user_id: int):
        user, data, early = await _owner_form(request)
        if early:
            return early
        try:
            admin_store.set_blocked(user_id, data.get("blocked") == "1")
        except ValueError as exc:
            return render_admin(request, user, [str(exc)], status=400)
        except LookupError:
            return Response("Not found.", status_code=404)
        return _see_other("/admin")

    return app
