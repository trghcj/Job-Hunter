"""CLI: python -m jobhunter migrate | run | test-email | backfill"""

import argparse
import logging
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config

from jobhunter.db import connect_with_retry, make_engine

REPO_ROOT = Path(__file__).resolve().parents[2]


def alembic_config(url: str | None = None) -> Config:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    if url:
        cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def cmd_migrate(_: argparse.Namespace) -> int:
    engine = make_engine()
    connect_with_retry(engine)
    command.upgrade(alembic_config(engine.url.render_as_string(hide_password=False)), "head")
    print("Migrations applied.")
    return 0


def cmd_not_yet(args: argparse.Namespace) -> int:
    print(f"'{args.command}' is not implemented yet (see the roadmap).", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    parser = argparse.ArgumentParser(prog="jobhunter")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="apply database migrations").set_defaults(func=cmd_migrate)
    for name in ("run", "test-email", "backfill"):
        sub.add_parser(name).set_defaults(func=cmd_not_yet)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
