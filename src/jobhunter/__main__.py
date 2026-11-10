"""CLI: python -m jobhunter migrate | run | set-titles | test-email | backfill"""

import argparse
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.orm import Session

from jobhunter.db import SearchProfile, connect_with_retry, make_engine

REPO_ROOT = Path(__file__).resolve().parents[2]


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


def _recipients(session: Session) -> list[str]:
    from jobhunter.user_settings import get_setting

    to = get_setting(session, "digest_to", []) or []
    return [to] if isinstance(to, str) else list(to)


def _email_sender(session: Session):
    from jobhunter.notify.email_resend import DEFAULT_FROM, ResendSender
    from jobhunter.settings import get_settings
    from jobhunter.user_settings import get_setting

    key = get_settings().resend_api_key.get_secret_value()
    if not key:
        return None
    return ResendSender(key, get_setting(session, "digest_from", DEFAULT_FROM) or DEFAULT_FROM)


def _deliver(session: Session, digest, now):
    from jobhunter.db import Run
    from jobhunter.notify import email_resend
    from jobhunter.notify.email_resend import DeliveryResult, deliver_digest
    from jobhunter.pipeline import mark_emailed
    from jobhunter.usage import DbBudget

    sender = _email_sender(session)
    if sender is None:
        result = DeliveryResult(False, error="RESEND_API_KEY is not set") if digest \
            else DeliveryResult(False)
    else:
        budget = DbBudget(session, {email_resend.PROVIDER: email_resend.LIMITS})
        result = deliver_digest(
            digest, sender, _recipients(session), budget, now,
            mark=lambda ids: mark_emailed(session, ids, now),
        )
    session.add(Run(
        source="email", started_at=now, finished_at=datetime.now(UTC),
        ok=result.error is None, fetched=len(digest), inserted=result.count, error=result.error,
    ))
    session.commit()
    return result


def cmd_run(_: argparse.Namespace) -> int:
    from jobhunter.pipeline import DEFAULT_MAX_JOBS, run_pipeline
    from jobhunter.settings import get_settings
    from jobhunter.sources.registry import build_adapters
    from jobhunter.user_settings import get_setting

    now = datetime.now(UTC)
    with _session() as session:
        sources = get_setting(session, "sources", {}) or {}
        digest_cfg = get_setting(session, "digest", {}) or {}
        max_age = int(get_setting(session, "max_age_days", 14))
        adapters = build_adapters(session, get_settings(), sources)
        summary = run_pipeline(
            session, adapters, now=now, max_age_days=max_age,
            max_jobs=int(digest_cfg.get("max_jobs", DEFAULT_MAX_JOBS)),
        )
        delivery = _deliver(session, summary.digest, now)

    for s in summary.sources:
        status = "ok" if s.ok else f"FAILED ({s.error})"
        print(f"{s.source}: fetched {s.fetched}, new {s.inserted}, {status}")
    print(f"checked {summary.scored} recent unsent jobs, {summary.passed} passed the filters")
    if delivery.sent:
        print(f"digest emailed: {delivery.count} jobs")
    elif delivery.error:
        print(f"digest NOT sent: {delivery.error}")
    else:
        print("no new jobs, no email sent")
    ok = all(s.ok for s in summary.sources) and delivery.error is None
    return 0 if ok else 1


def cmd_set_email(args: argparse.Namespace) -> int:
    """Temporary until the settings page: where the digest goes."""
    from jobhunter.user_settings import set_setting

    with _session() as session:
        set_setting(session, "digest_to", args.addresses)
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
    """Temporary until the settings page (step 7): set titles on the first active profile."""
    with _session() as session:
        profile = session.scalars(
            select(SearchProfile).where(SearchProfile.active.is_(True)).order_by(SearchProfile.id)
        ).first()
        if profile is None:
            print("No active search profile. Run `migrate` first.", file=sys.stderr)
            return 1
        profile.titles = [t.strip() for t in args.titles if t.strip()]
        session.commit()
        print(f"Titles for profile '{profile.name}': {profile.titles}")
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
    sub.add_parser("run", help="fetch, filter and build the digest").set_defaults(func=cmd_run)
    titles = sub.add_parser("set-titles", help="set job titles (until the settings page)")
    titles.add_argument("titles", nargs="+")
    titles.set_defaults(func=cmd_set_titles)
    email = sub.add_parser("set-email", help="set digest recipient(s) (until the settings page)")
    email.add_argument("addresses", nargs="+")
    email.set_defaults(func=cmd_set_email)
    sub.add_parser("test-email", help="send a sample digest").set_defaults(func=cmd_test_email)
    sub.add_parser("backfill").set_defaults(func=cmd_not_yet)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
