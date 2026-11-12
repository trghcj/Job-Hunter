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
