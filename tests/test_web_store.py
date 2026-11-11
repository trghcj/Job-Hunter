"""DbSettingsStore against a real Postgres (TEST_DATABASE_URL); skipped when unset."""

from sqlalchemy.orm import sessionmaker

from jobhunter.user_settings import load_active_searches
from jobhunter.web.forms import ProfileForm
from jobhunter.web.store import DbSettingsStore


def test_store_round_trip_feeds_the_pipeline(db_session):
    store = DbSettingsStore(sessionmaker(bind=db_session.get_bind()))
    form = store.load()  # seeds defaults on an empty database
    assert form.titles == [] and (form.min_years, form.max_years) == (0, 1)

    store.save(ProfileForm(
        titles=["Python Developer"], cities=["Pune"], work_modes=["remote"], min_years=0,
        max_years=2, exclude_words=["unpaid"], digest_to=["me@example.com"], max_jobs=10,
        max_age_days=7,
    ))
    again = store.load()
    assert again.titles == ["Python Developer"] and again.max_jobs == 10
    assert again.digest_to == ["me@example.com"] and again.max_age_days == 7

    searches = load_active_searches(db_session, 1)
    assert searches[0].titles == ["Python Developer"] and searches[0].max_years == 2
