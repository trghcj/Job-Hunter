"""The marigold look: one stylesheet with no blue, the logo, and a shared top bar."""

import colorsys
import re
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from jobhunter.web.app import create_app  # noqa: E402
from tests.test_web_admin import FakeAdmin  # noqa: E402
from tests.test_web_app import FakeStore  # noqa: E402
from tests.web_fakes import FakeAccounts, FakeVerifier, NoJobs, app_settings, sign_in  # noqa: E402,E501

STATIC = Path(__file__).resolve().parents[1] / "src" / "jobhunter" / "web" / "static"


@pytest.fixture
def client():
    accounts = FakeAccounts()
    app = create_app(store_factory=lambda uid: FakeStore(), jobs_store=NoJobs(),
                     admin_store=FakeAdmin(accounts), verifier=FakeVerifier(),
                     accounts=accounts, settings=app_settings())
    return TestClient(app)


def _is_blue(hex_colour: str) -> bool:
    h = hex_colour.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    hue, _, sat = colorsys.rgb_to_hls(r, g, b)
    return 180 <= hue * 360 <= 260 and sat > 0.25


def test_static_css_served(client):
    r = client.get("/static/app.css")
    assert r.status_code == 200 and "text/css" in r.headers["content-type"]
    for token in ("--bg", "--card", "--ink", "--muted", "--border", "--accent", "--accent-ink",
                  "--ok", "--danger"):
        assert token in r.text
    assert "#E09F3E".lower() in r.text.lower() and "prefers-color-scheme: dark" in r.text


def test_no_blue_in_css():
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    colours = re.findall(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b", css)
    assert colours and not [c for c in colours if _is_blue(c)]


def test_no_blue_in_logo():
    svg = (STATIC / "logo.svg").read_text(encoding="utf-8")
    colours = re.findall(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b", svg)
    assert colours and not [c for c in colours if _is_blue(c)]


@pytest.mark.parametrize("name", ["logo.svg", "favicon.svg"])
def test_logo_and_favicon_served_as_svg(client, name):
    r = client.get(f"/static/{name}")
    assert r.status_code == 200 and "image/svg+xml" in r.headers["content-type"]
    assert 'viewBox="0 0 64 64"' in r.text and "<text" not in r.text and "href=" not in r.text


def test_admin_link_only_for_owner(client):
    other = TestClient(client.app)
    sign_in(client, "owner")
    sign_in(other, "someone")
    assert 'href="/admin"' in client.get("/").text
    assert 'href="/admin"' not in other.get("/").text


@pytest.mark.parametrize("path", ["/", "/settings", "/admin"])
def test_pages_use_base_layout(client, path):
    sign_in(client, "owner")
    page = client.get(path).text
    assert 'class="topbar"' in page and '<link rel="stylesheet" href="/static/app.css">' in page
    assert '<link rel="icon" href="/static/favicon.svg" type="image/svg+xml">' in page
    assert "Sign out" in page


def test_login_page_uses_layout_without_user_menu(client):
    page = client.get("/login").text
    assert 'class="topbar"' in page and "Sign out" not in page


def test_text_boxes_have_no_resize_handle_and_file_button_is_styled():
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    assert "resize: vertical" not in css and "resize: none" in css
    assert "::file-selector-button" in css
    assert "scrollbar-color" in css
