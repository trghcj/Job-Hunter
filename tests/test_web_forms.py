import pytest

from jobhunter.web.forms import (
    ProfileForm,
    parse_admin_form,
    parse_form,
    parse_gmail_form,
    parse_lines,
)


def fields(**overrides) -> dict[str, str]:
    base = {
        "titles": "Backend Engineer\nPython Developer",
        "cities": "Bangalore, Pune",
        "min_years": "0",
        "max_years": "1",
        "exclude_words": "unpaid\nintern",
        "digest_to": "me@example.com",
        "max_jobs": "25",
        "max_age_days": "14",
    }
    base.update(overrides)
    return base


def test_parse_lines_trims_dedupes_and_keeps_order():
    assert parse_lines("  A \n\nb\na\nC ") == ["A", "b", "C"]


def test_parse_lines_with_commas():
    assert parse_lines("Pune, Mumbai\nDelhi", commas=True) == ["Pune", "Mumbai", "Delhi"]


def test_titles_split_on_commas_and_lines():
    form, errors = parse_form(
        fields(titles="Backend Engineer , Python Engineer , AI Engineer\nData Engineer"),
        ["remote"],
    )
    assert not errors
    assert form.titles == ["Backend Engineer", "Python Engineer", "AI Engineer", "Data Engineer"]


def test_valid_form():
    form, errors = parse_form(fields(), ["onsite", "remote"])
    assert errors == []
    assert form == ProfileForm(
        titles=["Backend Engineer", "Python Developer"],
        cities=["Bangalore", "Pune"],
        work_modes=["onsite", "remote"],
        min_years=0,
        max_years=1,
        exclude_words=["unpaid", "intern"],
        digest_to=["me@example.com"],
        max_jobs=25,
        max_age_days=14,
    )


def test_min_above_max_rejected():
    _, errors = parse_form(fields(min_years="3", max_years="1"), ["remote"])
    assert any("minimum" in e.lower() for e in errors)


def test_non_numbers_rejected():
    _, errors = parse_form(fields(max_years="abc", max_jobs=""), ["remote"])
    assert len(errors) == 2


def test_no_work_mode_rejected_and_unknown_modes_dropped():
    _, errors = parse_form(fields(), [])
    assert any("work mode" in e.lower() for e in errors)
    form, errors = parse_form(fields(), ["remote", "moon"])
    assert form.work_modes == ["remote"]


def test_bad_email_rejected():
    _, errors = parse_form(fields(digest_to="not-an-email"), ["remote"])
    assert any("email" in e.lower() for e in errors)


def test_too_long_title_rejected():
    _, errors = parse_form(fields(titles="x" * 101), ["remote"])
    assert any("title" in e.lower() for e in errors)


def test_admin_form_sources_and_watchlist():
    enabled, watchlist, errors = parse_admin_form(
        {"watchlist": "Groww | greenhouse | groww\nCRED | lever | cred"},
        ["greenhouse", "lever", "not-a-source"],
    )
    assert errors == []
    assert enabled["greenhouse"] and enabled["lever"]
    assert not enabled["adzuna"] and "not-a-source" not in enabled
    assert [c.slug for c in watchlist] == ["groww", "cred"]


def test_admin_form_no_sources_rejected_and_bad_watchlist_line_reported():
    _, _, errors = parse_admin_form({"watchlist": "Groww | workday | groww"}, [])
    assert any("source" in e.lower() for e in errors)
    assert any("Watchlist line 1" in e for e in errors)


def test_profile_form_has_no_shared_settings():
    form, _ = parse_form(fields(), ["remote"])
    assert not hasattr(form, "sources") and not hasattr(form, "watchlist")


def test_gmail_form_valid_with_spaced_app_password():
    address, password, senders, errors = parse_gmail_form({
        "gmail_address": " Me@Gmail.com ", "app_password": "abcd efgh ijkl mnop",
        "senders": "jobalerts-noreply@linkedin.com, naukri.com",
    })
    assert errors == []
    assert address == "me@gmail.com" and password == "abcdefghijklmnop"
    assert senders == ["jobalerts-noreply@linkedin.com", "naukri.com"]


def test_gmail_form_blank_password_keeps_saved_and_defaults_senders():
    _, password, senders, errors = parse_gmail_form({"gmail_address": "me@gmail.com"})
    assert errors == [] and password is None
    assert "naukri.com" in senders


@pytest.mark.parametrize(
    "fields",
    [{"gmail_address": "not-an-email"},
     {"gmail_address": "me@gmail.com", "app_password": "my real password 1"},
     {"gmail_address": "me@gmail.com", "senders": "linkedin"}],
)
def test_gmail_form_errors(fields):
    assert parse_gmail_form(fields)[3]


def test_empty_titles_allowed_but_warned_elsewhere():
    form, errors = parse_form(fields(titles=""), ["remote"])
    assert errors == [] and form.titles == []


def test_more_than_15_titles_rejected():
    many = "\n".join(f"Title {i}" for i in range(16))
    _, errors = parse_form(fields(titles=many), ["remote"])
    assert "At most 15 job titles." in errors
    _, ok = parse_form(fields(titles="\n".join(f"Title {i}" for i in range(15))), ["remote"])
    assert ok == []
