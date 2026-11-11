"""Shared normalizer: derive city, country, work mode and dedupe_key from any RawJob.

Source adapters stay dumb; every source-independent cleanup happens here.
"""

import hashlib
import re
from dataclasses import dataclass, replace

from jobhunter.models import RawJob

# Canonical city -> aliases. Multi-word names are matched before their substrings
# (Navi Mumbai before Mumbai, Greater Noida before Noida) by sorting on length.
CITY_ALIASES: dict[str, list[str]] = {
    "Bengaluru": ["bengaluru", "bangalore"],
    "Mumbai": ["mumbai", "bombay"],
    "Navi Mumbai": ["navi mumbai"],
    "Thane": ["thane"],
    "Delhi": ["new delhi", "delhi", "delhi ncr"],
    "Gurugram": ["gurugram", "gurgaon"],
    "Noida": ["noida"],
    "Greater Noida": ["greater noida"],
    "Faridabad": ["faridabad"],
    "Ghaziabad": ["ghaziabad"],
    "Hyderabad": ["hyderabad", "secunderabad"],
    "Chennai": ["chennai", "madras"],
    "Pune": ["pune"],
    "Kolkata": ["kolkata", "calcutta"],
    "Ahmedabad": ["ahmedabad"],
    "Jaipur": ["jaipur"],
    "Kochi": ["kochi", "cochin", "ernakulam"],
    "Thiruvananthapuram": ["thiruvananthapuram", "trivandrum"],
    "Chandigarh": ["chandigarh"],
    "Mohali": ["mohali"],
    "Indore": ["indore"],
    "Coimbatore": ["coimbatore"],
    "Bhubaneswar": ["bhubaneswar"],
    "Nagpur": ["nagpur"],
    "Lucknow": ["lucknow"],
    "Vadodara": ["vadodara", "baroda"],
    "Mysuru": ["mysuru", "mysore"],
    "Visakhapatnam": ["visakhapatnam", "vizag"],
    "Surat": ["surat"],
    "Bhopal": ["bhopal"],
    "Goa": ["goa", "panaji"],
}

_CITY_PATTERNS: list[tuple[re.Pattern[str], str]] = sorted(
    (
        (re.compile(rf"\b{re.escape(alias)}\b"), city)
        for city, aliases in CITY_ALIASES.items()
        for alias in aliases
    ),
    key=lambda pair: -len(pair[0].pattern),
)

INDIAN_STATES = (
    "andhra pradesh", "assam", "bihar", "chhattisgarh", "gujarat", "haryana", "himachal pradesh",
    "jharkhand", "karnataka", "kerala", "madhya pradesh", "maharashtra", "odisha", "punjab",
    "rajasthan", "tamil nadu", "telangana", "uttar pradesh", "uttarakhand", "west bengal",
)
_INDIA_RE = re.compile(r"\b(india|" + "|".join(INDIAN_STATES) + r")\b")

# Title + location are explicit labels; check them first. Description is noisy, so it only
# counts when the phrasing clearly describes the role.
_HEAD_HYBRID = re.compile(r"\bhybrid\b")
_HEAD_ONSITE = re.compile(r"\bon[- ]?site\b|\bin[- ]office\b|work from office|\bwfo\b")
_HEAD_REMOTE = re.compile(r"\bremote\b|work from home|\bwfh\b")
_DESC_REMOTE = re.compile(
    r"\b(fully|100%) remote\b|\bremote[- ](role|position|job|work|first|opportunity|friendly)\b"
    r"|work from home|\bwfh\b|work from anywhere"
)
_DESC_NOT_REMOTE = re.compile(r"\b(not|no)\s+(a\s+)?(fully\s+)?remote\b")

_LEGAL_SUFFIXES = {
    "pvt", "private", "ltd", "limited", "inc", "llp", "llc", "corp", "corporation", "co", "plc",
}


@dataclass(frozen=True)
class NormalizedJob:
    raw: RawJob
    city: str | None
    country: str | None  # "IN" or None (unknown / elsewhere)
    work_mode: str  # onsite | hybrid | remote | unknown
    dedupe_key: str


def _clean(text: str | None) -> str:
    return " ".join((text or "").split())


def detect_city(location_text: str | None) -> str | None:
    text = _clean(location_text).lower()
    for pattern, city in _CITY_PATTERNS:
        if pattern.search(text):
            return city
    return None


def detect_country(location_text: str | None, city: str | None) -> str | None:
    if city:
        return "IN"
    if _INDIA_RE.search(_clean(location_text).lower()):
        return "IN"
    return None


def detect_work_mode(title: str | None, location_text: str | None, description: str | None) -> str:
    head = f"{_clean(title)} {_clean(location_text)}".lower()
    for pattern, mode in (
        (_HEAD_HYBRID, "hybrid"), (_HEAD_ONSITE, "onsite"), (_HEAD_REMOTE, "remote"),
    ):
        if pattern.search(head):
            return mode

    desc = _clean(description).lower()
    if _HEAD_HYBRID.search(desc):
        return "hybrid"
    if _HEAD_ONSITE.search(desc):
        return "onsite"
    if _DESC_REMOTE.search(desc) and not _DESC_NOT_REMOTE.search(desc):
        return "remote"
    return "unknown"


def _company_key(company: str) -> str:
    words = re.sub(r"[^\w\s]", " ", company.lower()).split()
    while words and words[-1] in _LEGAL_SUFFIXES:
        words.pop()
    return " ".join(words)


def make_dedupe_key(company: str, title: str, city: str | None) -> str:
    parts = (_company_key(company), _clean(title).lower(), _clean(city).lower())
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()


def normalize_job(job: RawJob) -> NormalizedJob:
    cleaned = replace(job, title=_clean(job.title), company=_clean(job.company))
    city = detect_city(cleaned.location_text)
    return NormalizedJob(
        raw=cleaned,
        city=city,
        country=detect_country(cleaned.location_text, city),
        work_mode=detect_work_mode(cleaned.title, cleaned.location_text, cleaned.description),
        dedupe_key=make_dedupe_key(cleaned.company, cleaned.title, city),
    )
