"""Things the daily run on GitHub Actions needs that unit tests elsewhere would not catch."""

import tomllib
from pathlib import Path

from jobhunter.settings import Settings

ROOT = Path(__file__).parent.parent


def test_daily_run_dependencies_are_core():
    """GitHub Actions installs only the core dependencies (`pip install -e .`), and the
    daily run signs the email action links with itsdangerous (jobhunter.actions)."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    names = {d.split(">")[0].split("[")[0].split("=")[0].strip() for d in project["dependencies"]}
    assert "itsdangerous" in names


def test_default_gemini_model_is_current():
    """gemini-2.5-flash answers 404 to new keys; 3.1 flash lite answers in seconds."""
    assert Settings(_env_file=None).gemini_model == "gemini-3.1-flash-lite"


def test_daily_run_pins_its_runner():
    """ubuntu-latest moves to new Ubuntu versions on GitHub's schedule; a pinned image
    keeps the daily run from breaking on an OS change."""
    workflow = (ROOT / ".github" / "workflows" / "run.yml").read_text(encoding="utf-8")
    assert "runs-on: ubuntu-24.04" in workflow and "ubuntu-latest" not in workflow


def test_web_app_reuses_one_gemini_client():
    """One Gemini HTTP client for the web app, not a new connection per draft."""
    import pytest

    pytest.importorskip("fastapi")
    from jobhunter.web.app import create_app
    from tests.test_web_app import FakeStore
    from tests.web_fakes import FakeAccounts, FakeVerifier, NoJobs, app_settings

    app = create_app(store_factory=lambda uid: FakeStore(), jobs_store=NoJobs(),
                     verifier=FakeVerifier(), accounts=FakeAccounts(),
                     settings=app_settings(gemini_api_key="k" * 20))
    first = app.state.draft_client()
    assert first is not None and app.state.draft_client() is first
