"""CLI: python -m jobhunter migrate | run | set-titles | test-email | backfill"""

import argparse
import logging
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.orm import Session

from jobhunter.db import SearchProfile, connect_with_retry, make_engine
from jobhunter.settings import get_settings

REPO_ROOT = Path(__file__).resolve().parents[2]
log = logging.getLogger("jobhunter")


def alembic_config(url: str | None = None) -> Config:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    if url:
        cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def _session() -> Session:
    engine = make_engine()
    connect_with_retry(engine)
    return Session(engine)


def cmd_migrate(_: argparse.Namespace) -> int:
    engine = make_engine()
    connect_with_retry(engine)
    command.upgrade(alembic_config(engine.url.render_as_string(hide_password=False)), "head")
    from jobhunter.user_settings import seed_defaults

    with Session(engine) as session:
        seed_defaults(session)
    print("Migrations applied, defaults seeded.")
    return 0


def _owner_id(session: Session) -> int:
    from sqlalchemy import select

    from jobhunter.db import OWNER_PLACEHOLDER_ID, User

    found = session.scalars(select(User.id).where(User.is_owner.is_(True)).order_by(User.id))
    return found.first() or OWNER_PLACEHOLDER_ID


def _recipients(session: Session, user_id: int | None = None) -> list[str]:
    """A user's digest addresses (the owner's when no user is given)."""
    from jobhunter.user_settings import get_user_setting

    uid = _owner_id(session) if user_id is None else user_id
    to = get_user_setting(session, uid, "digest_to", []) or []
    return [to] if isinstance(to, str) else list(to)


def _email_everyone(session: Session) -> bool:
    """Digests go to all users only once digest_from is an address on your own domain;
    Resend's shared test sender (@resend.dev) can only reach the account owner."""
    from email.utils import parseaddr

    from jobhunter.user_settings import get_setting

    address = parseaddr(str(get_setting(session, "digest_from", "") or ""))[1].lower()
    return bool(address) and not address.endswith("@resend.dev")


def _email_sender(session: Session):
    from jobhunter.notify.email_resend import DEFAULT_FROM, ResendSender
    from jobhunter.settings import get_settings
    from jobhunter.user_settings import get_setting

    key = get_settings().resend_api_key.get_secret_value()
    if not key:
        return None
    try:
        from_addr = get_setting(session, "digest_from", DEFAULT_FROM) or DEFAULT_FROM
    except Exception:  # database unusable: still able to send alerts
        session.rollback()
        from_addr = DEFAULT_FROM
    return ResendSender(key, from_addr)


def _action_links(settings, user_id: int):
    """Signed Save / Applied / Not interested links for this user's digest, or None when
    SETTINGS_KEY or PUBLIC_URL is missing (the email is then sent without them)."""
    key, base = settings.settings_key.get_secret_value(), settings.public_url
    if not key or not base:
        return None
    from jobhunter.actions import ACTIONS, action_key, action_url, make_token

    signing = action_key(key)

    def links(job_id: int) -> dict[str, str]:
        return {a: action_url(base, make_token(signing, user_id, job_id, a)) for a in ACTIONS}

    return links


def _digest_sender(session: Session, user_id: int):
    """(sender, problem): the user's own Gmail when they chose it, else Resend."""
    from jobhunter.crypto import CryptoError
    from jobhunter.mail_account import gmail_sender

    try:
        own = gmail_sender(session, user_id, get_settings().settings_key.get_secret_value())
    except CryptoError:
        return None, "the saved Gmail password could not be decrypted (SETTINGS_KEY changed?)"
    if own is not None:
        return own, None
    sender = _email_sender(session)
    return sender, None if sender else "RESEND_API_KEY is not set"


def _deliver(session: Session, user_id: int, digest, now, run_source: str = "email"):
    from jobhunter.db import Run
    from jobhunter.notify import email_resend
    from jobhunter.notify.email_gmail import GMAIL_LIMITS, GMAIL_PROVIDER
    from jobhunter.notify.email_resend import DeliveryResult, deliver_digest
    from jobhunter.pipeline import mark_emailed
    from jobhunter.usage import DbBudget

    sender, problem = _digest_sender(session, user_id)
    if sender is None:
        result = DeliveryResult(False, error=problem) if digest else DeliveryResult(False)
    else:
        budget = DbBudget(session, {email_resend.PROVIDER: email_resend.LIMITS,
                                    GMAIL_PROVIDER: GMAIL_LIMITS})
        result = deliver_digest(
            digest, sender, _recipients(session, user_id), budget, now,
            mark=lambda ids: mark_emailed(session, user_id, ids, now),
            links=_action_links(get_settings(), user_id),
        )
    session.add(Run(
        source=run_source, started_at=now, finished_at=datetime.now(UTC),
        ok=result.error is None, fetched=len(digest), inserted=result.count, error=result.error,
    ))
    session.commit()
    return result


def _wants_own_gmail(session: Session, user_id: int) -> bool:
    from jobhunter.mail_account import get_account, send_digest_enabled

    return send_digest_enabled(session, user_id) and get_account(session, user_id) is not None


def deliver_digests(session: Session, summary, now: datetime) -> list[str]:
    """Email each user's digest. The owner always; others from their own Gmail when they
    chose it, or with your own sending domain (see _email_everyone). Returns problems; one
    failure does not stop the rest."""
    from jobhunter.pipeline import UserResult

    owner = _owner_id(session)
    everyone = _email_everyone(session)
    problems: list[str] = []
    # The owner's "email" run row is always written (even with nothing to send), because
    # backup runs (--once-per-day) look for it.
    users = {owner: UserResult(), **summary.users}
    for user_id, result in users.items():
        is_owner = user_id == owner
        if not (is_owner or everyone or _wants_own_gmail(session, user_id)):
            continue
        if not is_owner and not _recipients(session, user_id):
            continue  # this user set no digest address: My jobs only
        source = "email" if is_owner else f"email:{user_id}"
        delivery = _deliver(session, user_id, result.digest, now, source)
        who = "your" if is_owner else f"user {user_id}'s"
        if delivery.sent:
            print(f"{who} digest emailed: {delivery.count} jobs")
        elif delivery.error:
            print(f"{who} digest NOT sent: {delivery.error}")
            problems.append(f"{who} digest email: {delivery.error}")
    return problems


def _secrets() -> list[str]:
    from sqlalchemy.engine import make_url

    from jobhunter.settings import get_settings

    s = get_settings()
    values = [
        s.adzuna_app_key.get_secret_value(), s.resend_api_key.get_secret_value(),
        s.gemini_api_key.get_secret_value(), s.settings_key.get_secret_value(),
        s.session_secret.get_secret_value(),
    ]
    for url in (s.database_url, s.test_database_url):
        try:
            values.append(make_url(url).password or "")
        except Exception:
            pass
    return [v for v in values if v]


def _send_alert(problems: list[str], now: datetime, session: Session | None) -> None:
    """Best effort: a failing alert must not hide the original problem."""
    from jobhunter.notify.reports import build_alert, redact
    from jobhunter.settings import get_settings

    try:
        clean = [redact(p, _secrets()) for p in problems]
        to = [a.strip() for a in get_settings().alert_to.split(",") if a.strip()]
        if not to and session is not None:
            try:
                to = _recipients(session)
            except Exception:
                session.rollback()
        sender = _email_sender(session) if session is not None else _email_sender_no_db()
        if sender is None or not to:
            log.error("Cannot send failure alert: no Resend key or recipient")
            return
        subject, html_body, text_body = build_alert(clean, now)
        sender.send(to, subject, html_body, text_body)
        print("failure alert emailed")
    except Exception as exc:
        log.error("Failure alert could not be sent: %s", type(exc).__name__)


def _email_sender_no_db():
    from jobhunter.notify.email_resend import ResendSender
    from jobhunter.settings import get_settings

    key = get_settings().resend_api_key.get_secret_value()
    return ResendSender(key) if key else None


def _maybe_heartbeat(session: Session, now: datetime, force: bool = False) -> bool:
    from jobhunter.notify import email_resend
    from jobhunter.notify.email_resend import IST
    from jobhunter.notify.reports import build_heartbeat, heartbeat_due, weekly_stats
    from jobhunter.usage import DbBudget
    from jobhunter.user_settings import get_setting, set_setting

    today = now.astimezone(IST).date()
    last_raw = get_setting(session, "last_heartbeat")
    last = date.fromisoformat(last_raw) if last_raw else None
    if not force and not heartbeat_due(last, today):
        return False
    sender, to = _email_sender(session), _recipients(session)
    if sender is None or not to:
        return False
    if not DbBudget(session, {email_resend.PROVIDER: email_resend.LIMITS}).try_spend("resend"):
        return False
    subject, html_body, text_body = build_heartbeat(weekly_stats(session, now), now)
    sender.send(to, subject, html_body, text_body)
    set_setting(session, "last_heartbeat", today.isoformat())
    print("weekly heartbeat emailed")
    return True


def _ran_today(session: Session, now: datetime) -> bool:
    from jobhunter.pipeline import ran_successfully_today

    return ran_successfully_today(session, now)


def _llm_client(settings):
    """Gemini for the daily review, or None when GEMINI_API_KEY is not set."""
    from jobhunter.llm.gemini import client_from_settings

    return client_from_settings(settings)


def cmd_run(args: argparse.Namespace) -> int:
    from jobhunter.mail_account import gmail_adapters
    from jobhunter.pipeline import run_pipeline
    from jobhunter.settings import get_settings
    from jobhunter.sources.catalog import is_enabled
    from jobhunter.sources.registry import build_adapters
    from jobhunter.sources.watchlist import companies_from_setting
    from jobhunter.user_settings import get_setting

    now = datetime.now(UTC)
    problems: list[str] = []
    session: Session | None = None
    try:
        session = _session()
        if getattr(args, "once_per_day", False) and _ran_today(session, now):
            print("already ran successfully today (IST); skipping this backup run")
            session.close()
            return 0
        settings = get_settings()
        sources = get_setting(session, "sources", {}) or {}
        watchlist = companies_from_setting(get_setting(session, "company_watchlist", []))
        adapters = build_adapters(session, settings, sources, watchlist)
        gmail = (gmail_adapters(session, settings.settings_key.get_secret_value())
                 if is_enabled(sources, "gmail_alerts") else [])
        if not adapters and not gmail:
            problems.append("no job sources are enabled or configured")
        summary = run_pipeline(session, adapters, gmail, now=now, llm=_llm_client(settings))

        for s in summary.sources:
            status = "ok" if s.ok else f"FAILED ({s.error})"
            print(f"{s.source}: fetched {s.fetched}, new {s.inserted}, {status}")
            if not s.ok:
                problems.append(f"{s.source}: {s.error}")
        for user_id, result in summary.users.items():
            print(f"user {user_id}: checked {result.scored} recent unsent jobs, "
                  f"{result.passed} passed the filters")
        problems += deliver_digests(session, summary, now)

        try:
            _maybe_heartbeat(session, now)
        except Exception as exc:
            problems.append(f"weekly heartbeat: {type(exc).__name__}: {exc}")
    except (Exception, SystemExit) as exc:
        problems.append(f"run crashed: {type(exc).__name__}: {exc}")
        log.error("Run crashed: %s", type(exc).__name__)

    if problems:
        print("problems:\n  " + "\n  ".join(problems))
        _send_alert(problems, now, session)
    if session is not None:
        session.close()
    return 1 if problems else 0


def cmd_heartbeat(_: argparse.Namespace) -> int:
    """Send the weekly heartbeat now (for testing)."""
    with _session() as session:
        sent = _maybe_heartbeat(session, datetime.now(UTC), force=True)
    if not sent:
        print("Heartbeat not sent: set RESEND_API_KEY and a digest recipient.", file=sys.stderr)
    return 0 if sent else 1


def cmd_set_email(args: argparse.Namespace) -> int:
    """Temporary until the settings page: where the digest goes."""
    from jobhunter.user_settings import set_user_setting

    with _session() as session:
        set_user_setting(session, _owner_id(session), "digest_to", args.addresses)
        session.commit()
    print(f"Digest will be sent to: {', '.join(args.addresses)}")
    return 0


def cmd_test_email(_: argparse.Namespace) -> int:
    from jobhunter.notify.email_resend import build_digest
    from jobhunter.pipeline import DigestJob

    now = datetime.now(UTC)
    sample = DigestJob(
        job_id=0, title="Sample Backend Engineer (test email)", company="JobHunter",
        location_text="Bengaluru, Karnataka", work_mode="hybrid", url="https://example.com",
        salary_text="₹4L–₹6L a year", posted_at=now, source="test", score=1.0,
    )
    with _session() as session:
        sender, to = _email_sender(session), _recipients(session)
    if sender is None or not to:
        print("Set RESEND_API_KEY in .env and run `set-email` first.", file=sys.stderr)
        return 1
    subject, html_body, text_body = build_digest([sample], now)
    try:
        sender.send(to, "[test] " + subject, html_body, text_body)
    except Exception as exc:
        print(f"Test email failed: {exc}", file=sys.stderr)
        return 1
    print(f"Test email sent to {', '.join(to)}")
    return 0


def cmd_set_titles(args: argparse.Namespace) -> int:
    """Set the owner's job titles (other users use the web app)."""
    with _session() as session:
        profile = session.scalars(
            select(SearchProfile)
            .where(SearchProfile.user_id == _owner_id(session), SearchProfile.active.is_(True))
            .order_by(SearchProfile.id)
        ).first()
        if profile is None:
            print("No active search profile. Run `migrate` first.", file=sys.stderr)
            return 1
        profile.titles = [t.strip() for t in args.titles if t.strip()]
        session.commit()
        print(f"Titles for profile '{profile.name}': {profile.titles}")
    return 0


def cmd_rescore(_: argparse.Namespace) -> int:
    """Find skills in jobs saved before Phase 4, then re-score every user's jobs."""
    from jobhunter.pipeline import backfill_job_skills, rescore_all

    with _session() as session:
        filled = backfill_job_skills(session)
        users = rescore_all(session, datetime.now(UTC))
    print(f"Skills found for {filled} job(s); re-scored {users} user(s).")
    return 0


def cmd_make_owner(args: argparse.Namespace) -> int:
    """Make your signed-in account (OWNER_EMAIL) the owner if the placeholder was never
    claimed. Shows what would change unless --apply is given."""
    from jobhunter.accounts import make_owner

    with _session() as session:
        fix = make_owner(session, get_settings().owner_email, apply=args.apply)
    if not fix.ok:
        print(fix.message, file=sys.stderr)
        return 1
    print(f"Your account {fix.user_id} gets {fix.emailed_marks} emailed marks and "
          f"{fix.private_jobs} private jobs from the owner placeholder, which is then deleted.")
    print(fix.message)
    return 0


def cmd_not_yet(args: argparse.Namespace) -> int:
    print(f"'{args.command}' is not implemented yet (see the roadmap).", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)  # its URLs can carry API keys
    parser = argparse.ArgumentParser(prog="jobhunter")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="apply database migrations").set_defaults(func=cmd_migrate)
    run = sub.add_parser("run", help="fetch, filter and email the digest")
    run.add_argument(
        "--once-per-day", action="store_true",
        help="skip if a run already succeeded today (IST); used by backup schedule times",
    )
    run.set_defaults(func=cmd_run)
    titles = sub.add_parser("set-titles", help="set job titles (until the settings page)")
    titles.add_argument("titles", nargs="+")
    titles.set_defaults(func=cmd_set_titles)
    email = sub.add_parser("set-email", help="set digest recipient(s) (until the settings page)")
    email.add_argument("addresses", nargs="+")
    email.set_defaults(func=cmd_set_email)
    sub.add_parser("test-email", help="send a sample digest").set_defaults(func=cmd_test_email)
    sub.add_parser("heartbeat", help="send the weekly heartbeat now").set_defaults(
        func=cmd_heartbeat
    )
    sub.add_parser("rescore", help="find skills in older jobs and re-score everyone").set_defaults(
        func=cmd_rescore)
    owner = sub.add_parser("make-owner", help="make your account (OWNER_EMAIL) the owner")
    owner.add_argument("--apply", action="store_true", help="make the change (default: preview)")
    owner.set_defaults(func=cmd_make_owner)
    sub.add_parser("backfill").set_defaults(func=cmd_not_yet)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
