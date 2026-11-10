from pathlib import Path

from jobhunter.db import SearchProfile
from jobhunter.sources.base import Search
from jobhunter.user_settings import load_defaults, profile_to_search
from tests.test_phase0 import offline_sql

CONFIG = Path(__file__).resolve().parents[1] / "config.yaml"


def test_migration_creates_settings_tables():
    sql = offline_sql()
    assert "CREATE TABLE search_profiles (" in sql
    assert "CREATE TABLE app_settings (" in sql
    assert "min_years <= max_years" in sql


def test_load_defaults_reads_repo_config():
    profile, settings = load_defaults(CONFIG)
    assert profile["titles"] == []
    assert profile["cities"] == []
    assert profile["work_modes"] == ["onsite", "hybrid", "remote"]
    assert (profile["min_years"], profile["max_years"]) == (0, 1)
    assert profile["exclude_words"] == ["unpaid"]
    assert settings["min_final_score"] == 0.55
    assert settings["digest"] == {"hour_ist": 7, "minute": 30, "max_jobs": 25}
    assert settings["company_watchlist"] == []
    assert settings["llm"]["max_calls_per_run"] == 30
    assert "search_profile" not in settings and "exclude_words" not in settings


def test_load_defaults_missing_file_uses_builtins(tmp_path):
    profile, settings = load_defaults(tmp_path / "nope.yaml")
    assert (profile["min_years"], profile["max_years"]) == (0, 1)
    assert settings["min_final_score"] == 0.55


def test_profile_to_search():
    row = SearchProfile(
        name="default",
        titles=["Python Developer"],
        cities=["Pune"],
        work_modes=["remote"],
        min_years=0,
        max_years=2,
        exclude_words=["unpaid"],
    )
    assert profile_to_search(row) == Search(
        titles=["Python Developer"],
        cities=["Pune"],
        work_modes=["remote"],
        min_years=0,
        max_years=2,
        exclude_words=["unpaid"],
    )
