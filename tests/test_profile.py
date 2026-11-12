"""Resume profile: contact details removed, PDF limits, first line kept away from the AI,
per-user storage, and reviews cleared when the profile changes."""

import io
from datetime import UTC, datetime
from pathlib import Path

import pytest

from jobhunter.accounts import Identity, sign_in
from jobhunter.db import Job, JobScore
from jobhunter.profile import (
    MAX_PDF_BYTES,
    MAX_TEXT,
    ProfileError,
    clean_text,
    digest_fit,
    for_llm,
    get_flag,
    load_profile,
    pdf_text,
    save_profile,
)

NOW = datetime(2026, 11, 12, 3, 0, tzinfo=UTC)
FIXTURE = Path(__file__).parent / "fixtures" / "resume_synthetic.pdf"
NO_TEXT = "Couldn't read text from this PDF, paste your resume text instead."


def test_clean_text_removes_contact_details_anywhere():
    text = ("Asha Verma\nReach me at asha.verma@example.com or +91 98765 43210, "
            "alt 98765-43210, office (080) 2345 6789. Code: https://github.com/x "
            "and linkedin.com/in/asha, site www.asha.dev. Python since 2023, 3 years.")
    out = clean_text(text)
    for gone in ("asha.verma@example.com", "98765", "2345 6789", "github.com", "linkedin.com",
                 "www.asha.dev", "https://"):
        assert gone not in out
    assert "Python since 2023, 3 years." in out


def test_clean_text_caps_length_and_whitespace():
    assert len(clean_text("word " * 10000)) == MAX_TEXT
    assert clean_text("a   b\n\n\n\nc") == "a b\n\nc"


def test_pdf_text_reads_fixture():
    text = pdf_text(FIXTURE.read_bytes())
    assert "Django" in text
    assert "asha.verma@example.com" not in clean_text(text)


def _pdf(pages: int, text: bool = False) -> bytes:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_pdf_limits():
    with pytest.raises(ProfileError, match="PDF"):
        pdf_text(b"hello")
    with pytest.raises(ProfileError, match="2 MB"):
        pdf_text(b"%PDF-" + b"0" * MAX_PDF_BYTES)
    with pytest.raises(ProfileError, match="10 pages"):
        pdf_text(_pdf(11))
    with pytest.raises(ProfileError) as err:
        pdf_text(_pdf(1))
    assert str(err.value) == NO_TEXT
    with pytest.raises(ProfileError) as err:
        pdf_text(b"%PDF-1.4 garbage that is not a pdf")
    assert str(err.value) == NO_TEXT


def test_for_llm_drops_first_line():
    assert for_llm("\n\nAsha Verma\nPython developer\nDjango") == "Python developer\nDjango"


def _user(session, n: int) -> int:
    return sign_in(session, Identity(f"uid{n}", f"u{n}@gmail.com", None), "", NOW).user_id


def test_save_profile_extracts_skills_and_isolates_users(db_session):
    a, b = _user(db_session, 1), _user(db_session, 2)
    profile = save_profile(db_session, a, "Asha\nPython and Django, mail a@b.co", NOW)
    assert profile.skills == ("Django", "Python") and "a@b.co" not in profile.text
    assert load_profile(db_session, a).text == profile.text
    assert load_profile(db_session, b) is None


def test_save_profile_clears_reviews(db_session):
    a = _user(db_session, 1)
    job = Job(dedupe_key="k", source="s", url="https://x", title="T", company="C")
    db_session.add(job)
    db_session.commit()
    db_session.add(JobScore(user_id=a, job_id=job.id, passed_filters=True, llm_fit=4,
                            llm_json={"fit": 4, "reason": "Good"}))
    db_session.commit()
    save_profile(db_session, a, "Python", NOW)
    row = db_session.get(JobScore, (a, job.id))
    db_session.refresh(row)
    assert row.llm_fit is None and row.llm_json is None


def test_flags_default(db_session):
    a = _user(db_session, 1)
    assert get_flag(db_session, a) is False
    assert digest_fit(db_session, a) == "good"


def test_clean_text_drops_leftover_link_labels():
    text = ("Asha Verma\n| |LinkedIn |GitHub |Portfolio\nEmail · Phone\nEDUCATION\n"
            "Built a GitHub bot in Python")
    out = clean_text(text)
    assert "LinkedIn" not in out and "Portfolio" not in out and "Email · Phone" not in out
    assert out.splitlines() == ["Asha Verma", "EDUCATION", "Built a GitHub bot in Python"]
