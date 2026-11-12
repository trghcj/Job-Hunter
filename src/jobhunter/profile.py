"""A user's profile: their resume as cleaned text, used to rank jobs by fit.

Only cleaned text is kept (emails, phone numbers and links removed); the uploaded PDF is
never stored or logged. The first line (usually the person's name) is also dropped before
anything is sent to an AI (`for_llm`).
"""

import io
import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import update
from sqlalchemy.orm import Session

from jobhunter.db import JobScore
from jobhunter.matching.fit import LEVEL_MIN
from jobhunter.matching.skills import extract_skills
from jobhunter.user_settings import get_user_setting, set_user_setting

MAX_PDF_BYTES = 2_000_000
MAX_PAGES = 10
MAX_TEXT = 15_000
NO_TEXT = "Couldn't read text from this PDF, paste your resume text instead."
DEFAULT_DIGEST_FIT = "good"

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_DOMAIN = re.compile(
    r"\b[\w-]+(?:\.[\w-]+)*\.(?:com|in|io|dev|org|me|co|ai|app|xyz|tech|site)\b(?:/\S*)?",
    re.IGNORECASE)
_PHONE_LIKE = re.compile(r"\+?\(?\d[\d\s().-]{6,}\d")
_MIN_PHONE_DIGITS = 10  # "2019 - 2023" (8 digits) is a date range, not a phone number
# A line left with only link labels once the links are gone: "| |LinkedIn |GitHub |Portfolio".
_LABELS_ONLY = re.compile(
    r"[\s|•·,/–-]*(?:(?:linkedin|github|gitlab|portfolio|website|web|email|e-mail|mail|phone|"
    r"mobile|tel|leetcode|codeforces|codechef|hackerrank|kaggle|twitter|x|blog|medium|"
    r"behance|dribbble|resume)[\s|•·,/:–-]*)*", re.IGNORECASE)


def _drop_label_lines(text: str) -> str:
    return "\n".join(line for line in text.split("\n")
                     if not (line.strip() and _LABELS_ONLY.fullmatch(line)))


class ProfileError(ValueError):
    """A resume we cannot use; the message is shown to the user."""


@dataclass(frozen=True)
class Profile:
    text: str
    skills: tuple[str, ...]
    updated_at: str | None


def _drop_phone(m: re.Match[str]) -> str:
    return "" if sum(c.isdigit() for c in m.group(0)) >= _MIN_PHONE_DIGITS else m.group(0)


def clean_text(text: str) -> str:
    """Resume text without contact details, with tidy whitespace, at most MAX_TEXT chars."""
    text = _EMAIL.sub("", text or "")
    text = _URL.sub("", text)
    text = _DOMAIN.sub("", text)
    text = _PHONE_LIKE.sub(_drop_phone, text)
    text = _drop_label_lines(text)
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:MAX_TEXT]


def pdf_text(data: bytes) -> str:
    """The text of a resume PDF (not yet cleaned). Raises ProfileError with a message for
    the user when the file is not a usable PDF."""
    if len(data) > MAX_PDF_BYTES:
        raise ProfileError("Resume PDF must be 2 MB or smaller.")
    if not data.startswith(b"%PDF-"):
        raise ProfileError("Upload a PDF file, or paste your resume text instead.")
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        if len(reader.pages) > MAX_PAGES:
            raise ProfileError("Resume PDF can have at most 10 pages.")
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except ProfileError:
        raise
    except Exception as exc:  # pypdf raises many kinds of errors on broken files
        raise ProfileError(NO_TEXT) from exc
    if not text.strip():
        raise ProfileError(NO_TEXT)
    return text


def for_llm(text: str) -> str:
    """Profile text for an AI prompt: without its first non-empty line (the name)."""
    lines = text.strip().splitlines()
    return "\n".join(lines[1:]).strip()


def load_profile(session: Session, user_id: int) -> Profile | None:
    value = get_user_setting(session, user_id, "profile")
    if not isinstance(value, dict) or not value.get("text"):
        return None
    return Profile(value["text"], tuple(value.get("skills") or ()), value.get("updated_at"))


def save_profile(session: Session, user_id: int, text: str, now: datetime) -> Profile:
    """Clean and store the profile, and forget AI reviews made for the old one."""
    clean = clean_text(text)
    profile = Profile(clean, tuple(sorted(extract_skills(clean), key=str.lower)),
                      now.isoformat())
    set_user_setting(session, user_id, "profile", {
        "text": profile.text, "skills": list(profile.skills), "updated_at": profile.updated_at})
    session.execute(update(JobScore).where(JobScore.user_id == user_id)
                    .values(llm_fit=None, llm_json=None))
    session.commit()
    return profile


def get_flag(session: Session, user_id: int) -> bool:
    """Whether the user lets Gemini review their top jobs (off unless they turned it on)."""
    return get_user_setting(session, user_id, "llm_review", False) is True


def digest_fit(session: Session, user_id: int) -> str:
    value = get_user_setting(session, user_id, "digest_fit", DEFAULT_DIGEST_FIT)
    return value if value in LEVEL_MIN else DEFAULT_DIGEST_FIT
