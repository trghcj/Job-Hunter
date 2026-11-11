"""My profile card: resume upload limits and errors, profile text, AI and digest choices."""

from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from jobhunter.web.app import create_app  # noqa: E402
from tests.test_web_app import FakeStore  # noqa: E402
from tests.web_fakes import (  # noqa: E402
    FakeAccounts,
    FakeVerifier,
    NoJobs,
    app_settings,
    page_csrf,
    sign_in,
)

RESUME = (Path(__file__).parent / "fixtures" / "resume_synthetic.pdf").read_bytes()


@pytest.fixture
def store():
    return FakeStore()


@pytest.fixture
def client(store):
    app = create_app(store_factory=lambda uid: store, jobs_store=NoJobs(),
                     verifier=FakeVerifier(), accounts=FakeAccounts(), settings=app_settings())
    return TestClient(app)


def upload(client, data: bytes, token: str | None = None):
    return client.post("/profile/resume", data={"csrf": token or page_csrf(client)},
                       files={"resume": ("cv.pdf", data, "application/pdf")},
                       follow_redirects=False)


def test_profile_card_shows_text_skills_and_options(client, store):
    store.profile = replace(store.profile, text="Python <b>dev</b>", skills=("Django", "Python"))
    sign_in(client)
    page = client.get("/settings").text
    assert 'id="profile"' in page and "Upload resume" in page
    assert "Python &lt;b&gt;dev&lt;/b&gt;" in page
    assert ">Django<" in page and ">Python<" in page
    assert "Let Gemini review my top 10 jobs each day" in page
    box = page.split('name="llm_review"')[1].split(">")[0]
    assert "checked" not in box
    for choice in ("Strong fits only", "Good and strong", "Everything that passed"):
        assert choice in page
    assert '<option value="good" selected' in page


def test_upload_resume_saves_cleaned_text(client, store):
    sign_in(client)
    r = upload(client, RESUME)
    assert r.status_code == 303 and r.headers["location"] == "/settings?profile=saved#profile"
    assert store.uploads == [len(RESUME)] and store.profile.text == "Text from the PDF"


def test_upload_errors_shown(client, store):
    sign_in(client)
    r = upload(client, b"hello")
    assert r.status_code == 400 and "Upload a PDF file" in r.text


def test_upload_over_2mb_rejected_before_store(client, store):
    sign_in(client)
    r = upload(client, b"%PDF-" + b"0" * 2_000_000)
    assert r.status_code == 400 and "2 MB" in r.text
    assert getattr(store, "uploads", []) == []


def test_upload_without_file(client, store):
    sign_in(client)
    r = client.post("/profile/resume", data={"csrf": page_csrf(client)})
    assert r.status_code == 400 and "Choose a PDF" in r.text


def test_profile_routes_need_csrf_and_sign_in(client, store):
    for path in ("/profile", "/profile/resume", "/profile/options"):
        r = client.post(path, data={"csrf": "x"}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/login"
    sign_in(client)
    for path in ("/profile", "/profile/resume", "/profile/options"):
        assert client.post(path, data={"csrf": "wrong"}).status_code == 403
    assert getattr(store, "uploads", []) == [] and store.profile.text == ""


def test_save_profile_text_and_options(client, store):
    sign_in(client)
    token = page_csrf(client)
    r = client.post("/profile", data={"csrf": token, "profile_text": "Python and SQL"},
                    follow_redirects=False)
    assert r.status_code == 303 and store.profile.text == "Python and SQL"
    r = client.post("/profile/options", data={"csrf": token, "llm_review": "1",
                                              "digest_fit": "strong"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/settings?profile=options#profile"
    assert store.profile.llm_review is True and store.profile.digest_fit == "strong"
    client.post("/profile/options", data={"csrf": token, "digest_fit": "good"})
    assert store.profile.llm_review is False  # unticked box
    r = client.post("/profile/options", data={"csrf": token, "digest_fit": "bogus"})
    assert r.status_code == 400 and store.profile.digest_fit == "good"
