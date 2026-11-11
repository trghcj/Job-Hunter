"""Small text helpers shared by source adapters."""

import html
import re
from datetime import UTC, datetime


def clean_html(text: str | None) -> str:
    """Strip tags and entities, collapse whitespace. Handles double-escaped HTML."""
    raw = text or ""
    if "&lt;" in raw:  # Greenhouse escapes its HTML once more
        raw = html.unescape(raw)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", raw)).split())


def fix_mojibake(text: str | None) -> str:
    """Repair UTF-8 text that was decoded as Latin-1/CP1252 (e.g. 'â€"' for '–')."""
    s = text or ""
    if not any(marker in s for marker in ("â", "Ã", "Â")):
        return s
    for codec in ("cp1252", "latin-1"):
        try:
            return s.encode(codec).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    return s


def parse_datetime(value: str | int | float | None, millis: bool = False) -> datetime | None:
    """ISO string or epoch number to an aware datetime (naive strings are taken as UTC)."""
    if value is None or value == "":
        return None
    try:
        if isinstance(value, int | float):
            return datetime.fromtimestamp(value / 1000 if millis else value, tz=UTC)
        dt = datetime.fromisoformat(str(value))
    except (ValueError, OSError, OverflowError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
