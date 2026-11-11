"""Settings page: single-user login, then one form for titles, experience, cities and digest.

Start with:  uvicorn jobhunter.web.app:create_app --factory
Needs WEB_PASSWORD and SESSION_SECRET (32+ random characters) in the environment.
"""

import hmac
import secrets
import time
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import sessionmaker
from starlette.middleware.sessions import SessionMiddleware

from jobhunter.settings import Settings, get_settings
from jobhunter.sources.catalog import SOURCE_LABELS
from jobhunter.sources.watchlist import format_watchlist
from jobhunter.web.forms import WORK_MODES, parse_form, parse_gmail_form
from jobhunter.web.store import DbSettingsStore, SettingsStore

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
SESSION_DAYS = 7
GMAIL_NOTICES = {
    "saved": "Gmail saved. Use Test connection to check it.",
    "removed": "Gmail account removed.",
}
SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; "
    "form-action 'self'; frame-ancestors 'none'",
}


def _csrf(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = request.session["csrf"] = secrets.token_urlsafe(32)
    return token


def _csrf_ok(request: Request, submitted: str | None) -> bool:
    expected = request.session.get("csrf")
    return bool(expected and submitted and hmac.compare_digest(expected, submitted))


def _logged_in(request: Request) -> bool:
    return request.session.get("auth") is True


def _see_other(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def create_app(
    store: SettingsStore | None = None,
    settings: Settings | None = None,
    login_delay: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> FastAPI:
    settings = settings or get_settings()
    password = settings.web_password.get_secret_value()
    secret = settings.session_secret.get_secret_value()
    if not password or len(secret) < 32:
        raise RuntimeError("Set WEB_PASSWORD and SESSION_SECRET (32+ characters) to start.")

    if store is None:
        from jobhunter.db import make_engine

        store = DbSettingsStore(sessionmaker(make_engine()),
                                settings_key=settings.settings_key.get_secret_value())

    app = FastAPI(title="JobHunter settings", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(
        SessionMiddleware,
        secret_key=secret,
        https_only=settings.web_https_only,
        same_site="lax",
        max_age=SESSION_DAYS * 86400,
        session_cookie="jobhunter_session",
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response: Response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        return response

    @app.get("/healthz")
    def healthz() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request):
        if _logged_in(request):
            return _see_other("/")
        return TEMPLATES.TemplateResponse(request, "login.html", {"csrf": _csrf(request)})

    @app.post("/login")
    async def login(request: Request):
        form = await request.form()
        if not _csrf_ok(request, form.get("csrf")):
            return Response("Form expired, reload the page.", status_code=403)
        given = str(form.get("password") or "")
        if not hmac.compare_digest(given.encode(), password.encode()):
            sleep(login_delay)  # slow down guessing
            ctx = {"csrf": _csrf(request), "error": "Wrong password."}
            return TEMPLATES.TemplateResponse(request, "login.html", ctx, status_code=401)
        request.session.clear()
        request.session["auth"] = True
        return _see_other("/")

    @app.post("/logout")
    async def logout(request: Request):
        form = await request.form()
        if _csrf_ok(request, form.get("csrf")):
            request.session.clear()
        return _see_other("/login")

    def render_settings(request: Request, form, errors=(), saved=False, status=200,
                        watchlist_text: str | None = None, gmail_errors=(),
                        gmail_message: str | None = None):
        ctx = {
            "csrf": _csrf(request),
            "form": form,
            "errors": list(errors),
            "saved": saved,
            "modes": WORK_MODES,
            "source_labels": SOURCE_LABELS,
            "watchlist_text": (
                watchlist_text if watchlist_text is not None else format_watchlist(form.watchlist)
            ),
            "mail": store.load_mail(),
            "gmail_errors": list(gmail_errors),
            "gmail_message": gmail_message,
            "runs": store.recent_runs(),
        }
        return TEMPLATES.TemplateResponse(request, "settings.html", ctx, status_code=status)

    @app.get("/", response_class=HTMLResponse)
    def settings_page(request: Request):
        if not _logged_in(request):
            return _see_other("/login")
        saved = request.query_params.get("saved") == "1"
        message = GMAIL_NOTICES.get(request.query_params.get("gmail", ""))
        return render_settings(request, store.load(), saved=saved, gmail_message=message)

    async def _checked_form(request: Request):
        """The posted form if logged in with a valid CSRF token, else the response to send."""
        if not _logged_in(request):
            return None, _see_other("/login")
        data = await request.form()
        if not _csrf_ok(request, data.get("csrf")):
            return None, Response("Form expired, reload the page.", status_code=403)
        return data, None

    @app.post("/gmail")
    async def save_gmail(request: Request):
        data, early = await _checked_form(request)
        if early:
            return early
        fields = {k: str(v) for k, v in data.items() if isinstance(v, str)}
        address, app_password, senders, errors = parse_gmail_form(fields)
        if not errors:
            try:
                store.save_mail(address, app_password, senders)
            except ValueError as exc:
                errors = [str(exc)]
        if errors:
            return render_settings(request, store.load(), gmail_errors=errors, status=400)
        return _see_other("/?gmail=saved#gmail")

    @app.post("/gmail/test")
    async def test_gmail(request: Request):
        data, early = await _checked_form(request)
        if early:
            return early
        return render_settings(request, store.load(), gmail_message=store.test_mail())

    @app.post("/gmail/delete")
    async def delete_gmail(request: Request):
        data, early = await _checked_form(request)
        if early:
            return early
        store.delete_mail()
        return _see_other("/?gmail=removed#gmail")

    @app.post("/")
    async def save_settings(request: Request):
        if not _logged_in(request):
            return _see_other("/login")
        data = await request.form()
        if not _csrf_ok(request, data.get("csrf")):
            return Response("Form expired, reload the page.", status_code=403)
        fields = {k: str(v) for k, v in data.items() if isinstance(v, str)}
        modes = [str(m) for m in data.getlist("work_modes")]
        sources = [str(s) for s in data.getlist("sources")] if "sources_section" in data else None
        form, errors = parse_form(fields, modes, sources)
        if errors:
            return render_settings(request, form, errors, status=400,
                                   watchlist_text=fields.get("watchlist", ""))
        store.save(form)
        return _see_other("/?saved=1")

    return app
