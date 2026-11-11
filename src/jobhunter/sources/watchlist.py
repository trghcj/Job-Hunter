"""Company watchlist: which public careers boards to read.

Stored in app_settings['company_watchlist'] and edited on the settings page; the repo file
data/company_watchlist.yaml only seeds it on first run.
"""

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

BOARD_SITES = ("greenhouse", "lever", "ashby", "workday", "smartrecruiters")
DEFAULT_PATH = Path(__file__).resolve().parents[3] / "data" / "company_watchlist.yaml"
MAX_COMPANIES = 500
_CASELESS = {"greenhouse", "lever", "ashby"}  # these sites' board names are lowercase
_SLUG_RULES = {
    "greenhouse": re.compile(r"^[a-z0-9][a-z0-9._-]{0,99}$"),
    "lever": re.compile(r"^[a-z0-9][a-z0-9._-]{0,99}$"),
    "ashby": re.compile(r"^[a-z0-9][a-z0-9._-]{0,99}$"),
    # tenant.wdN/Site, e.g. nvidia.wd5/NVIDIAExternalCareerSite
    "workday": re.compile(r"^[a-z0-9-]{1,60}\.wd\d{1,3}/[A-Za-z0-9_.-]{1,100}$"),
    "smartrecruiters": re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$"),
}


@dataclass(frozen=True)
class Company:
    name: str
    ats: str  # greenhouse | lever | ashby | workday | smartrecruiters
    slug: str  # the company's board name on that site


def _make(name: str, ats: str, slug: str) -> Company:
    ats = ats.strip().lower()
    slug = slug.strip()
    return Company(name.strip(), ats, slug.lower() if ats in _CASELESS else slug)


def _valid(c: Company) -> bool:
    rule = _SLUG_RULES.get(c.ats)
    return bool(c.name) and len(c.name) <= 80 and rule is not None and bool(rule.match(c.slug))


def companies_from_setting(value: Any) -> list[Company]:
    out: list[Company] = []
    seen: set[tuple[str, str]] = set()
    for item in value or []:
        if not isinstance(item, dict):
            continue
        c = _make(str(item.get("name", "")), str(item.get("ats", "")), str(item.get("slug", "")))
        if _valid(c) and (c.ats, c.slug) not in seen:
            seen.add((c.ats, c.slug))
            out.append(c)
    return out


def companies_to_setting(companies: list[Company]) -> list[dict[str, str]]:
    return [asdict(c) for c in companies]


def load_default_entries(path: Path = DEFAULT_PATH) -> list[tuple[Company, int]]:
    """(company, since): `since` is the watchlist version that added it (default 1)."""
    if not path.exists():
        return []
    entries: list[tuple[Company, int]] = []
    for item in yaml.safe_load(path.read_text(encoding="utf-8")) or []:
        found = companies_from_setting([item])
        if found:
            entries.append((found[0], int(item.get("since", 1))))
    return entries


def load_default_watchlist(path: Path = DEFAULT_PATH) -> list[Company]:
    return [c for c, _ in load_default_entries(path)]


def merge_new_defaults(
    current: list[Company], entries: list[tuple[Company, int]], seen_version: int
) -> list[Company]:
    """Your list plus default companies added after `seen_version`. Companies you removed
    from an earlier version are not brought back."""
    present = {(c.ats, c.slug) for c in current}
    added = [c for c, since in entries
             if since > seen_version and (c.ats, c.slug) not in present]
    return current + added


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
        c = _make(*parts)
        if not _valid(c):
            errors.append(
                f"Watchlist line {n}: ats must be one of {', '.join(BOARD_SITES)}; the slug "
                "uses letters, digits, '.', '_' and '-' (Workday: tenant.wdN/Site)."
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
