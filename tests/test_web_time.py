"""Times on the site are shown in India time (IST), whatever the server's clock zone."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from jobhunter.web.app import ist  # noqa: E402

TEMPLATES = Path(__file__).parents[1] / "src" / "jobhunter" / "web" / "templates"


def test_utc_time_shown_in_ist():
    assert ist(datetime(2026, 11, 12, 2, 0, tzinfo=UTC), "%d %b %H:%M") == "12 Nov 07:30"


def test_crosses_midnight_into_next_day():
    assert ist(datetime(2026, 11, 11, 20, 0, tzinfo=UTC), "%d %b %Y") == "12 Nov 2026"


def test_missing_time_is_blank_or_fallback():
    assert ist(None, "%d %b") == ""
    assert ist(None, "%d %b", "never") == "never"


def test_naive_time_treated_as_utc():
    assert ist(datetime(2026, 11, 12, 2, 0), "%H:%M") == "07:30"


def test_templates_never_format_raw_times():
    for page in TEMPLATES.glob("*.html"):
        assert ".strftime(" not in page.read_text(encoding="utf-8"), page.name
