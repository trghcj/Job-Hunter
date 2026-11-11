"""Company watchlist: which public careers boards to read.

Stored in app_settings['company_watchlist'] and edited on the settings page; the repo file
data/company_watchlist.yaml only seeds it on first run.
"""

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

BOARD_SITES = ("greenhouse", "lever", "ashby")
DEFAULT_PATH = Path(__file__).resolve().parents[3] / "data" / "company_watchlist.yaml"
MAX_COMPANIES = 500
_SLUG = re.compile(r"^[a-z0-9][a-z0-9._-]{0,99}$")


@dataclass(frozen=True)
class Company:
    name: str
    ats: str  # greenhouse | lever | ashby
    slug: str  # the company's board name on that site


def _valid(c: Company) -> bool:
    return bool(c.name) and len(c.name) <= 80 and c.ats in BOARD_SITES and bool(_SLUG.match(c.slug))


def companies_from_setting(value: Any) -> list[Company]:
    out: list[Company] = []
    seen: set[tuple[str, str]] = set()
    for item in value or []:
        if not isinstance(item, dict):
            continue
        c = Company(str(item.get("name", "")).strip(), str(item.get("ats", "")).strip().lower(),
                    str(item.get("slug", "")).strip().lower())
        if _valid(c) and (c.ats, c.slug) not in seen:
            seen.add((c.ats, c.slug))
            out.append(c)
    return out


def companies_to_setting(companies: list[Company]) -> list[dict[str, str]]:
    return [asdict(c) for c in companies]


def load_default_watchlist(path: Path = DEFAULT_PATH) -> list[Company]:
    if not path.exists():
        return []
    return companies_from_setting(yaml.safe_load(path.read_text(encoding="utf-8")))


def parse_watchlist_text(text: str | None) -> tuple[list[Company], list[str]]:
    """One company per line: 'Name | ats | slug'."""
    companies: list[Company] = []
    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    for n, line in enumerate((text or "").splitlines(), start=1):
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) != 3:
            errors.append(f"Watchlist line {n}: use 'Name | ats | slug'.")
            continue
        c = Company(parts[0], parts[1].lower(), parts[2].lower())
        if not _valid(c):
            errors.append(
                f"Watchlist line {n}: ats must be one of {', '.join(BOARD_SITES)} and the slug "
                "may only use letters, digits, '.', '_' and '-'."
            )
            continue
        if (c.ats, c.slug) not in seen:
            seen.add((c.ats, c.slug))
            companies.append(c)
    if len(companies) > MAX_COMPANIES:
        errors.append(f"Use at most {MAX_COMPANIES} companies.")
    return companies, errors


def format_watchlist(companies: list[Company]) -> str:
    return "\n".join(f"{c.name} | {c.ats} | {c.slug}" for c in companies)
