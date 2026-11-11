"""Hard filters: cheap, local checks that cut raw listings down before any scoring.

Every check returns a human-readable reason when it drops a job, stored in
job_scores.filter_reason so you can see why something never reached the digest.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from jobhunter.normalize import NormalizedJob, detect_city
from jobhunter.sources.base import Search

DEFAULT_MAX_AGE_DAYS = 14

# ---------------------------------------------------------------- titles

_TITLE_PHRASES = [
    (re.compile(r"\bsoftware development engineer\b"), "software engineer"),
    (re.compile(r"\bsde\b"), "software engineer"),
    (re.compile(r"\bback\s*end\b"), "backend"),
    (re.compile(r"\bfront\s*end\b"), "frontend"),
    (re.compile(r"\bfull\s*stack\b"), "fullstack"),
    (re.compile(r"\bmachine learning\b"), "ml"),
    (re.compile(r"\bartificial intelligence\b"), "ai"),
    (re.compile(r"\bnode\s*js\b"), "node"),
]
_TITLE_WORD_SYNONYMS = {"developer": "engineer", "programmer": "engineer", "dev": "engineer"}


def _title_tokens(title: str) -> set[str]:
    text = re.sub(r"[^\w+#]+", " ", title.lower())
    for pattern, replacement in _TITLE_PHRASES:
        text = pattern.sub(replacement, text)
    return {_TITLE_WORD_SYNONYMS.get(word, word) for word in text.split()}


def title_score(job_title: str, targets: list[str]) -> float:
    """Best share of a target title's words found in the job title (0..1)."""
    job_tokens = _title_tokens(job_title)
    best = 0.0
    for target in targets:
        wanted = _title_tokens(target)
        if wanted:
            best = max(best, len(wanted & job_tokens) / len(wanted))
    return best


def title_matches(job_title: str, targets: list[str]) -> bool:
    return title_score(job_title, targets) == 1.0


# ---------------------------------------------------------------- exclude words

def excluded_word(title: str, description: str | None, words: list[str]) -> str | None:
    text = f"{title} {description or ''}".lower()
    for word in words:
        w = word.strip().lower()
        if w and re.search(rf"\b{re.escape(w)}\b", text):
            return word
    return None


# ---------------------------------------------------------------- location

# "US" is matched case-sensitively so "contact us only" does not count.
_REGION = (
    r"(?:US|USA|U\.S\.A?\.?|(?i:united states|european union|eu|europe|uk|united kingdom"
    r"|canada|latam|emea|north america|americas?|australia))"
)
_NOT_INDIA = [
    re.compile(rf"\b{_REGION}[- ](?i:only|residents?|citizens?|based)\b"),
    re.compile(rf"(?i:must be (?:located|based|residing) in)(?: the)? {_REGION}(?![A-Za-z])"),
    re.compile(
        rf"(?i:candidates?(?: who are)? (?:based |located |residing )?(?:in|from))"
        rf"(?: the)? {_REGION}(?![A-Za-z])"
    ),
]


def excludes_india(description: str | None) -> bool:
    text = description or ""
    return any(p.search(text) for p in _NOT_INDIA)


def location_ok(job: NormalizedJob) -> tuple[bool, str | None, bool]:
    """(keep, reason, remote_unconfirmed). Remote roles that never mention India are kept
    but flagged so scoring can rank clearly India-based roles first."""
    if job.country == "IN":
        return True, None, False
    if job.work_mode == "remote":
        if excludes_india(job.raw.description):
            return False, "remote, not open to India", False
        return True, None, True
    return False, "not in India", False


# ---------------------------------------------------------------- experience

_SENIOR = re.compile(
    r"\b(senior|sr|lead|principal|manager|architect|head|director|staff|vp|vice president"
    r"|chief)\b"
)
# Level markers in titles: "Engineer II", "SDE 3", "MTS-2", "Intermediate".
_LEVEL = re.compile(
    r"\b(ii|iii|iv)\b|\b(?:sde|mts|engineer|developer)[- ]?([2-9])\b"
    r"|\b(intermediate|mid[- ]level|experienced)\b"
)
_ROMAN_LEVEL_YEARS = {"ii": 2, "iii": 4, "iv": 6}
SENIOR_MIN_YEARS = 5  # Senior/Staff/Lead/... titles usually ask for about 5+ years


def level_years(m: re.Match[str]) -> int:
    """Rough minimum years a level marker implies: II/2 -> 2, III/3 -> 4, IV/4+ -> 6."""
    roman, digit, word = m.groups()
    if roman:
        return _ROMAN_LEVEL_YEARS[roman]
    if digit:
        return {2: 2, 3: 4}.get(int(digit), 6)
    return 2
_ENTRY = re.compile(
    r"\b(freshers?|entry[- ]level|graduates?|trainee|junior|jr|associate|new grad|campus)\b"
)
_YEARS = r"(?:years?|yrs?)"
_RANGE = re.compile(rf"(\d{{1,2}})\s*\+?\s*(?:-|–|—|to)\s*(\d{{1,2}})\s*\+?\s*{_YEARS}")
_SINGLE = re.compile(
    rf"(?:(?:minimum|min\.?|at\s*least)\s*(?:of\s*)?)?(\d{{1,2}})\s*(?:\+|plus)?\s*{_YEARS}"
)
_EXPERIENCE_CONTEXT = re.compile(r"\bexp")


def _experience_mentions(text: str) -> list[tuple[int, int | None]]:
    """(min, max) years asked for; max is None for open-ended ("2+ years")."""
    found: list[tuple[int, int | None]] = []
    taken: list[range] = []

    def near_experience(m: re.Match[str]) -> bool:
        window = text[max(0, m.start() - 50) : m.end() + 50]
        return bool(_EXPERIENCE_CONTEXT.search(window))

    for m in _RANGE.finditer(text):
        if near_experience(m):
            found.append((int(m.group(1)), int(m.group(2))))
            taken.append(range(m.start(), m.end()))
    for m in _SINGLE.finditer(text):
        if any(m.start() in r for r in taken) or not near_experience(m):
            continue
        found.append((int(m.group(1)), None))
    return found


def check_experience(
    title: str, description: str | None, min_years: int, max_years: int
) -> tuple[bool, str | None, bool]:
    """(keep, reason, experience_unstated)."""
    title_l = title.lower()
    if max_years < SENIOR_MIN_YEARS and (m := _SENIOR.search(title_l)):
        return False, f"senior title ({m.group(1)})", False
    if (m := _LEVEL.search(title_l)) and level_years(m) > max_years:
        return False, f"level title ({m.group(0)})", False

    text = f"{title_l} {(description or '').lower()}"
    mentions = _experience_mentions(text)
    if mentions:
        asked_min = min(lo for lo, _ in mentions)
        if asked_min > max_years:
            return False, f"asks for {asked_min}+ years (your max is {max_years})", False
        uppers = [hi for _, hi in mentions]
        if all(hi is not None for hi in uppers) and max(uppers) < min_years:  # type: ignore[type-var]
            return False, f"experience {max(uppers)} years is below your minimum {min_years}", False
        return True, None, False

    if _ENTRY.search(text):
        if min_years >= 2:
            return False, f"entry-level role is below your minimum {min_years} years", False
        return True, None, False
    return True, None, True


# ---------------------------------------------------------------- recency

def too_old(posted_at: datetime | None, now: datetime, max_age_days: int) -> int | None:
    if posted_at is None:
        return None
    age = now - posted_at
    return age.days if age > timedelta(days=max_age_days) else None


# ---------------------------------------------------------------- all together

@dataclass(frozen=True)
class FilterResult:
    passed: bool
    reason: str | None = None
    title_score: float = 0.0
    experience_unstated: bool = False  # small score penalty later
    remote_unconfirmed: bool = False  # remote, never mentions India: small penalty later


# Typing the main city of a metro region also accepts its satellite cities.
CITY_REGIONS = {
    "Delhi": {"Delhi", "Gurugram", "Noida", "Greater Noida", "Faridabad", "Ghaziabad"},
    "Mumbai": {"Mumbai", "Navi Mumbai", "Thane"},
    "Hyderabad": {"Hyderabad"},
    "Chandigarh": {"Chandigarh", "Mohali"},
}


def _wanted_cities(cities: list[str]) -> set[str]:
    wanted: set[str] = set()
    for c in cities:
        if c.strip():
            city = detect_city(c) or c.strip().title()
            wanted |= CITY_REGIONS.get(city, {city})
    return wanted


def apply_filters(
    job: NormalizedJob,
    search: Search,
    now: datetime | None = None,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
) -> FilterResult:
    now = now or datetime.now(UTC)
    raw = job.raw

    if not search.titles:
        return FilterResult(False, "no target titles set")
    score = title_score(raw.title, search.titles)
    if score < 1.0:
        return FilterResult(False, "title does not match your titles", score)

    if word := excluded_word(raw.title, raw.description, search.exclude_words):
        return FilterResult(False, f"excluded word: {word}", score)

    ok, reason, remote_unconfirmed = location_ok(job)
    if not ok:
        return FilterResult(False, reason, score)

    if job.work_mode != "unknown" and job.work_mode not in search.work_modes:
        return FilterResult(False, f"work mode {job.work_mode} not wanted", score)

    cities = _wanted_cities(search.cities)
    # A job naming no city (location just "India") is treated as open anywhere in India.
    if cities and job.work_mode != "remote" and job.city and job.city not in cities:
        return FilterResult(False, f"city {job.city} not in your cities", score)

    ok, reason, unstated = check_experience(
        raw.title, raw.description, search.min_years, search.max_years
    )
    if not ok:
        return FilterResult(False, reason, score)

    if (days := too_old(raw.posted_at, now, max_age_days)) is not None:
        return FilterResult(False, f"posted {days} days ago, older than {max_age_days}", score)

    return FilterResult(True, None, score, unstated, remote_unconfirmed)
