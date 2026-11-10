"""Parse and validate the settings form. Pure functions: no web framework, no database."""

import re
from dataclasses import dataclass

WORK_MODES = ("onsite", "hybrid", "remote")
MAX_TITLES = 50
MAX_TITLE_LEN = 100
MAX_RECIPIENTS = 5
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass
class ProfileForm:
    titles: list[str]
    cities: list[str]
    work_modes: list[str]
    min_years: int
    max_years: int
    exclude_words: list[str]
    digest_to: list[str]
    max_jobs: int
    max_age_days: int


def parse_lines(text: str | None, commas: bool = False) -> list[str]:
    """One item per line (and per comma if asked); trimmed, deduped ignoring case."""
    raw = re.split(r"[\n,]" if commas else r"\n", text or "")
    seen: set[str] = set()
    out: list[str] = []
    for item in (" ".join(r.split()) for r in raw):
        if item and item.lower() not in seen:
            seen.add(item.lower())
            out.append(item)
    return out


def _int(fields: dict[str, str], name: str, label: str, lo: int, hi: int,
         errors: list[str]) -> int:
    try:
        value = int((fields.get(name) or "").strip())
    except ValueError:
        errors.append(f"{label} must be a whole number.")
        return lo
    if not lo <= value <= hi:
        errors.append(f"{label} must be between {lo} and {hi}.")
    return value


def parse_form(fields: dict[str, str], work_modes: list[str]) -> tuple[ProfileForm, list[str]]:
    """Returns the parsed form (always, so it can be re-shown) and a list of errors."""
    errors: list[str] = []

    titles = parse_lines(fields.get("titles"))
    if len(titles) > MAX_TITLES:
        errors.append(f"Use at most {MAX_TITLES} job titles.")
    if any(len(t) > MAX_TITLE_LEN for t in titles):
        errors.append(f"Each job title must be at most {MAX_TITLE_LEN} characters.")

    modes = [m for m in WORK_MODES if m in work_modes]
    if not modes:
        errors.append("Pick at least one work mode.")

    min_years = _int(fields, "min_years", "Minimum years", 0, 40, errors)
    max_years = _int(fields, "max_years", "Maximum years", 0, 40, errors)
    if min_years > max_years:
        errors.append("Minimum years cannot be more than maximum years.")

    digest_to = parse_lines(fields.get("digest_to"), commas=True)
    if len(digest_to) > MAX_RECIPIENTS:
        errors.append(f"Use at most {MAX_RECIPIENTS} email addresses.")
    bad = [e for e in digest_to if not _EMAIL.match(e)]
    if bad:
        errors.append(f"Not a valid email address: {', '.join(bad)}")

    form = ProfileForm(
        titles=titles,
        cities=parse_lines(fields.get("cities"), commas=True),
        work_modes=modes,
        min_years=min_years,
        max_years=max_years,
        exclude_words=parse_lines(fields.get("exclude_words"), commas=True),
        digest_to=digest_to,
        max_jobs=_int(fields, "max_jobs", "Jobs per email", 1, 100, errors),
        max_age_days=_int(fields, "max_age_days", "Maximum job age", 1, 60, errors),
    )
    return form, errors
