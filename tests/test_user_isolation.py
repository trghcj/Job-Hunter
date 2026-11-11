"""One user's settings, profiles and Gmail never leak to another (real Postgres)."""

from datetime import UTC, datetime

from cryptography.fernet import Fernet
from sqlalchemy.orm import sessionmaker

from jobhunter.accounts import Identity, set_blocked, sign_in
from jobhunter.db import Run
from jobhunter.mail_account import gmail_adapters, mail_view, save_account
from jobhunter.user_settings import active_user_searches, load_active_searches
from jobhunter.web.forms import ProfileForm
from jobhunter.web.store import UserStore

KEY = Fernet.generate_key().decode()
NOW = datetime(2026, 11, 12, 3, 0, tzinfo=UTC)


def new_user(session, n: int) -> int:
    return sign_in(session, Identity(f"uid{n}", f"u{n}@gmail.com", None), "", NOW).user_id


def form(**over) -> ProfileForm:
    base = dict(titles=["Python Developer"], cities=["Pune"], work_modes=["remote"],
                min_years=0, max_years=2, exclude_words=[], digest_to=["a@example.com"],
                max_jobs=10, max_age_days=7)
    base.update(over)
    return ProfileForm(**base)


def stores(db_session, *user_ids):
    factory = sessionmaker(bind=db_session.get_bind())
    return [UserStore(factory, uid, KEY) for uid in user_ids]


def test_new_user_gets_default_profile(db_session):
    uid = new_user(db_session, 1)
    (store,) = stores(db_session, uid)
    loaded = store.load()
    assert loaded.titles == [] and (loaded.min_years, loaded.max_years) == (0, 1)
    assert loaded.max_jobs == 25 and loaded.max_age_days == 14


def test_profiles_are_per_user(db_session):
    a, b = new_user(db_session, 1), new_user(db_session, 2)
    sa, sb = stores(db_session, a, b)
    sa.save(form())
    assert sa.load().titles == ["Python Developer"]
    assert sb.load().titles == []
    assert load_active_searches(db_session, b)[0].titles == []


def test_user_settings_are_per_user(db_session):
    a, b = new_user(db_session, 1), new_user(db_session, 2)
    sa, sb = stores(db_session, a, b)
    sa.save(form(max_jobs=40, digest_to=["a@example.com"]))
    assert sa.load().max_jobs == 40 and sb.load().max_jobs == 25
    assert sb.load().digest_to == []


def test_gmail_is_per_user(db_session):
    a, b = new_user(db_session, 1), new_user(db_session, 2)
    sa, sb = stores(db_session, a, b)
    sa.save_mail("a@gmail.com", "abcdefghijklmnop", ["naukri.com"])
    assert sa.load_mail().address == "a@gmail.com"
    assert sb.load_mail().address == "" and not sb.load_mail().has_password
    sb.delete_mail()
    assert mail_view(db_session, a).has_password


def test_gmail_adapters_one_per_user_skipping_blocked(db_session):
    a, b = new_user(db_session, 1), new_user(db_session, 2)
    save_account(db_session, a, "a@gmail.com", "abcdefghijklmnop", ["naukri.com"], KEY)
    save_account(db_session, b, "b@gmail.com", "abcdefghijklmnop", ["naukri.com"], KEY)
    set_blocked(db_session, b, True)
    adapters = gmail_adapters(db_session, KEY)
    assert [(uid, ad.address) for uid, ad in adapters] == [(a, "a@gmail.com")]


def test_gmail_wrong_key_gives_broken_source(db_session):
    a = new_user(db_session, 1)
    save_account(db_session, a, "a@gmail.com", "abcdefghijklmnop", ["naukri.com"], KEY)
    [(uid, adapter)] = gmail_adapters(db_session, Fernet.generate_key().decode())
    assert uid == a and adapter.name == "gmail_alerts" and not hasattr(adapter, "address")


def test_active_user_searches_skips_blocked_and_titleless(db_session):
    a, b, c = new_user(db_session, 1), new_user(db_session, 2), new_user(db_session, 3)
    sa, sb, _ = stores(db_session, a, b, c)
    sa.save(form(titles=["Backend Engineer"]))
    sb.save(form(titles=["AI Engineer"]))
    set_blocked(db_session, b, True)
    found = active_user_searches(db_session)
    assert list(found) == [a] and found[a][0].titles == ["Backend Engineer"]


def test_runs_hide_other_users_gmail_rows(db_session):
    a, b = new_user(db_session, 1), new_user(db_session, 2)
    for source in ("adzuna", f"gmail_alerts:{a}", f"gmail_alerts:{b}"):
        db_session.add(Run(source=source, started_at=NOW, ok=True))
    db_session.commit()
    (sa,) = stores(db_session, a)
    assert {r.source for r in sa.recent_runs()} == {"adzuna", f"gmail_alerts:{a}"}


def test_runs_hide_other_users_email_rows(db_session):
    a, b = new_user(db_session, 1), new_user(db_session, 2)
    for source in ("email", f"email:{a}", f"email:{b}"):
        db_session.add(Run(source=source, started_at=NOW, ok=True))
    db_session.commit()
    (sa,) = stores(db_session, a)
    assert {r.source for r in sa.recent_runs()} == {"email", f"email:{a}"}


def test_runs_hide_other_users_ai_review_rows(db_session):
    a, b = new_user(db_session, 1), new_user(db_session, 2)
    for source in ("adzuna", f"llm:{a}", f"llm:{b}"):
        db_session.add(Run(source=source, started_at=NOW, ok=False, error="HTTP 429"))
    db_session.commit()
    (sa,) = stores(db_session, a)
    assert {r.source for r in sa.recent_runs()} == {"adzuna", f"llm:{a}"}
