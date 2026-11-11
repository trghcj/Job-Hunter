"""Parse job-alert emails (LinkedIn, Naukri) into RawJob.

Both parsers work from the job links, because the link and its text (the job title)
change least when a site redesigns its emails. The lines right after a job link give the
company, location, and (Naukri) experience and salary. When a layout change breaks a
parser, save the new email as a fixture first, then adjust.
"""

import re
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import parseaddr, parsedate_to_datetime
from html.parser import HTMLParser

from jobhunter.models import RawJob
from jobhunter.normalize import detect_city

_BLOCK = {"p", "div", "td", "tr", "br", "li", "table", "h1", "h2", "h3", "h4", "h5", "h6"}
_NOISE = re.compile(
    r"^(actively recruiting|easy apply|be an early applicant|promoted|new|apply( now)?|"
    r"view job|save|\d+ (connections?|school alum(ni)?|alumni)( work here)?|.*\bapplicants?)$",
    re.I,
)
_NOT_TITLES = ("see all", "view all", "apply", "view job", "unsubscribe", "manage", "search")


class _Tokens(HTMLParser):
    """Flattens HTML into (text, href) chunks, one per block or link."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tokens: list[tuple[str, str | None]] = []
        self._buf: list[str] = []
        self._href: str | None = None

    def _flush(self) -> None:
        text = " ".join("".join(self._buf).split())
        if text:
            self.tokens.append((text, self._href))
        self._buf = []

    def handle_starttag(self, tag, attrs):
        if tag == "a" or tag in _BLOCK:
            self._flush()
        if tag == "a":
            self._href = dict(attrs).get("href")

    def handle_endtag(self, tag):
        if tag == "a" or tag in _BLOCK:
            self._flush()
        if tag == "a":
            self._href = None

    def handle_data(self, data):
        self._buf.append(data)


def _tokens(html: str) -> list[tuple[str, str | None]]:
    parser = _Tokens()
    parser.feed(html or "")
    parser.close()
    return parser.tokens


def _title_like(text: str) -> bool:
    low = text.lower()
    return 2 <= len(text) <= 150 and not low.startswith(_NOT_TITLES)


def _lines_after(tokens, i: int, link: re.Pattern[str], limit: int = 6) -> list[str]:
    """Plain text lines after token i, up to the next job link."""
    out: list[str] = []
    for text, href in tokens[i + 1 :]:
        if href and link.search(href):
            break
        if href or _NOISE.match(text):
            continue
        out.append(text)
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------- LinkedIn

_LINKEDIN_JOB = re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(\d+)")


def parse_linkedin(html: str, sent: datetime | None) -> list[RawJob]:
    tokens = _tokens(html)
    jobs: list[RawJob] = []
    seen: set[str] = set()
    for i, (text, href) in enumerate(tokens):
        m = _LINKEDIN_JOB.search(href or "")
        if not m or m.group(1) in seen or not _title_like(text):
            continue
        seen.add(m.group(1))
        lines = _lines_after(tokens, i, _LINKEDIN_JOB)
        company, location = "Unknown", None
        if lines:
            if " · " in lines[0]:
                company, location = (p.strip() for p in lines[0].split(" · ", 1))
            else:
                company = lines[0]
                location = lines[1] if len(lines) > 1 else None
        jobs.append(RawJob(
            source="linkedin_email",
            source_job_id=m.group(1),
            url=f"https://www.linkedin.com/jobs/view/{m.group(1)}/",
            title=text,
            company=company,
            location_text=location,
            description=None,
            posted_at=sent,
        ))
    return jobs


# ---------------------------------------------------------------- Naukri

# Real alerts link to /jd/job-listings-<title>-<company>-<city>-<a>-to-<b>-years-<id>.
# The card text has no city or experience, so both are read from that link.
_NAUKRI_JOB = re.compile(
    r"(https?://(?:www\.)?naukri\.com/(?:jd/)?job-listings-([^?#\s\"']*?)-(\d{6,}))"
)
_SLUG_YEARS = re.compile(r"-(\d+)-to-(\d+)-years?$")
_LEGAL = {"pvt", "private", "ltd", "limited", "llp", "inc", "opc", "co"}
_EXPERIENCE = re.compile(r"^\d+\s*-\s*\d+\s*yrs?\.?$", re.I)
_SALARY = re.compile(r"lacs|lakh|lpa|₹|not disclosed|p\.a\.", re.I)
_JOB_TYPE = re.compile(r"^(internship|full[ -]time|part[ -]time|contract(ual)?|walk-?in)$", re.I)
_DURATION = re.compile(r"\bduration$", re.I)


def _is_place(text: str) -> bool:
    low = text.lower()
    return bool(detect_city(text)) or any(w in low for w in ("india", "remote", "ncr", "hybrid"))


def _slugify(text: str) -> str:
    return "-".join(re.findall(r"[a-z0-9]+", text.lower()))


def _from_slug(slug: str, company: str | None) -> tuple[str | None, str | None]:
    """(location, experience) from the link slug; location needs the company to anchor."""
    experience = location = None
    years = _SLUG_YEARS.search(slug)
    if years:
        experience = f"{years.group(1)}-{years.group(2)} Yrs"
        slug = slug[: years.start()]
    company_slug = _slugify(company or "")
    at = slug.rfind(company_slug) if company_slug else -1
    if at >= 0:
        words = slug[at + len(company_slug):].strip("-").split("-")
        while words and words[0] in _LEGAL:
            words.pop(0)
        location = " ".join(w.capitalize() for w in words if w) or None
    return location, experience


def _naukri_lines(lines: list[str]) -> dict[str, str | None]:
    found: dict[str, str | None] = dict.fromkeys(
        ("company", "location", "experience", "salary", "job_type"))
    for line in lines:
        if _EXPERIENCE.match(line):
            found["experience"] = line
        elif _SALARY.search(line):
            found["salary"] = None if "not disclosed" in line.lower() else line
        elif _JOB_TYPE.match(line):
            found["job_type"] = line
        elif _DURATION.search(line):
            continue
        elif found["company"] is None and not _is_place(line):
            found["company"] = line
        elif found["location"] is None and _is_place(line):
            found["location"] = line
    return found


def parse_naukri(html: str, sent: datetime | None) -> list[RawJob]:
    tokens = _tokens(html)
    jobs: list[RawJob] = []
    seen: set[str] = set()
    for i, (text, href) in enumerate(tokens):
        m = _NAUKRI_JOB.search(href or "")
        if not m or m.group(3) in seen or not _title_like(text):
            continue
        seen.add(m.group(3))
        found = _naukri_lines(_lines_after(tokens, i, _NAUKRI_JOB))
        slug_location, slug_years = _from_slug(m.group(2), found["company"])
        experience = found["experience"] or slug_years
        notes = [f"Experience: {experience}" if experience else None, found["job_type"]]
        jobs.append(RawJob(
            source="naukri_email",
            source_job_id=m.group(3),
            url=m.group(1),
            title=text,
            company=found["company"] or "Unknown",
            location_text=found["location"] or slug_location,
            description=". ".join(n for n in notes if n) or None,
            posted_at=sent,
            salary_text=found["salary"],
        ))
    return jobs


# ---------------------------------------------------------------- routing

PARSERS = {"linkedin.com": parse_linkedin, "naukri.com": parse_naukri}


def _sent(msg: EmailMessage) -> datetime | None:
    try:
        dt = parsedate_to_datetime(str(msg["Date"]))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def parse_alert_email(msg: EmailMessage) -> list[RawJob]:
    sender = parseaddr(str(msg.get("From", "")))[1].lower()
    domain = sender.rsplit("@", 1)[-1]
    parser = next((p for d, p in PARSERS.items() if domain == d or domain.endswith("." + d)),
                  None)
    if parser is None:
        return []
    body = msg.get_body(preferencelist=("html",))
    html = body.get_content() if body is not None else ""
    return parser(html, _sent(msg))
