from datetime import UTC, date, datetime

from jobhunter.notify.reports import (
    SourceWeek,
    WeeklyStats,
    build_alert,
    build_heartbeat,
    heartbeat_due,
    redact,
    stale_sources,
)

NOW = datetime(2026, 11, 15, 2, 0, tzinfo=UTC)  # Sunday 7:30 IST


# --- redaction -----------------------------------------------------------------------

def test_redact_removes_secrets_and_db_password():
    text = (
        "failed: postgresql://postgres.ref:Pa55word@host:5432/db key=re_abc123 "
        "and adzuna sekret"
    )
    out = redact(text, secrets=["re_abc123", "sekret", ""])
    assert "Pa55word" not in out and "re_abc123" not in out and "sekret" not in out
    assert "postgresql://postgres.ref:***@host:5432/db" in out


def test_redact_handles_password_fragment_in_host_error():
    # psycopg once printed part of a password when it contained '#'
    out = redact("failed to resolve host 'xyz@aws-0.pooler.supabase.com'", secrets=["abc#xyz"])
    assert "xyz@" not in out


# --- alert ----------------------------------------------------------------------------

def test_alert_lists_problems_escaped():
    subject, html, text = build_alert(["adzuna: HTTP 503", "email: <b>bad</b>"], NOW)
    assert subject == "JobHunter: run had problems · 15 Nov"
    assert "adzuna: HTTP 503" in text
    assert "&lt;b&gt;bad&lt;/b&gt;" in html and "<b>bad</b>" not in html


# --- heartbeat schedule ---------------------------------------------------------------

def test_heartbeat_due_on_sunday_once():
    sunday = date(2026, 11, 15)
    assert heartbeat_due(None, sunday)
    assert heartbeat_due(date(2026, 11, 8), sunday)
    assert not heartbeat_due(sunday, sunday)


def test_heartbeat_not_due_midweek_unless_overdue():
    wednesday = date(2026, 11, 18)
    assert not heartbeat_due(date(2026, 11, 15), wednesday)
    assert not heartbeat_due(None, wednesday)
    assert heartbeat_due(date(2026, 11, 8), wednesday)  # missed a Sunday: 10 days


# --- stale sources and heartbeat content ------------------------------------------------

def test_stale_sources_need_three_empty_days():
    stats = WeeklyStats(
        sources=[
            SourceWeek("adzuna", 7, 7, 700, 90, last3_fetched=[100, 0, 50]),
            SourceWeek("greenhouse", 7, 4, 10, 2, last3_fetched=[0, 0, 0]),
            SourceWeek("lever", 2, 2, 0, 0, last3_fetched=[0, 0]),
        ],
        new_jobs=92, emailed_jobs=40, digests_sent=6,
    )
    assert stale_sources(stats) == ["greenhouse"]


def test_heartbeat_content():
    stats = WeeklyStats(
        sources=[SourceWeek("adzuna", 7, 6, 650, 80, [0, 0, 0])],
        new_jobs=80, emailed_jobs=35, digests_sent=6,
    )
    subject, html, text = build_heartbeat(stats, NOW)
    assert subject == "JobHunter weekly: alive, 80 new jobs · 15 Nov"
    assert "35 jobs emailed in 6 digests" in text
    assert "adzuna" in text and "6/7 runs ok" in text
    assert "No results for 3 days: adzuna" in text
    assert "No results for 3 days" in html
