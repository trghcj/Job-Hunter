"""CLI: python -m jobhunter migrate | run | set-titles | test-email | backfill"""

import argparse
import logging
import sys
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


def cmd_run(_: argparse.Namespace) -> int:
    from jobhunter.pipeline import DEFAULT_MAX_JOBS, run_pipeline
    from jobhunter.settings import get_settings
    from jobhunter.sources.registry import build_adapters
    from jobhunter.user_settings import get_setting

    with _session() as session:
        sources = get_setting(session, "sources", {}) or {}
        digest_cfg = get_setting(session, "digest", {}) or {}
        max_age = int(get_setting(session, "max_age_days", 14))
        adapters = build_adapters(session, get_settings(), sources)
        summary = run_pipeline(
            session, adapters, max_age_days=max_age,
            max_jobs=int(digest_cfg.get("max_jobs", DEFAULT_MAX_JOBS)),
        )

    for s in summary.sources:
        status = "ok" if s.ok else f"FAILED ({s.error})"
        print(f"{s.source}: fetched {s.fetched}, new {s.inserted}, {status}")
    print(f"checked {summary.scored} recent unsent jobs, {summary.passed} passed the filters")
    print(f"digest: {len(summary.digest)} jobs (email sending arrives in step 6)")
    for d in summary.digest:
        print(f"  {d.score:.2f}  {d.title} | {d.company} | {d.location_text} | {d.work_mode}")
    return 0 if all(s.ok for s in summary.sources) else 1


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
    for name in ("test-email", "backfill"):
        sub.add_parser(name).set_defaults(func=cmd_not_yet)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
