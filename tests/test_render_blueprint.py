"""render.yaml: Render builds apply migrations, and the env list matches Google sign-in."""

from pathlib import Path

import yaml

BLUEPRINT = Path(__file__).resolve().parents[1] / "render.yaml"


def service() -> dict:
    return yaml.safe_load(BLUEPRINT.read_text(encoding="utf-8"))["services"][0]


def env_keys() -> set[str]:
    return {e["key"] for e in service()["envVars"]}


def test_render_build_runs_migrations():
    assert "python -m jobhunter migrate" in service()["buildCommand"]


def test_render_has_no_web_password():
    assert "WEB_PASSWORD" not in env_keys()


def test_render_lists_sign_in_settings():
    needed = {"DATABASE_URL", "SESSION_SECRET", "SETTINGS_KEY", "OWNER_EMAIL",
              "FIREBASE_API_KEY", "FIREBASE_AUTH_DOMAIN", "FIREBASE_PROJECT_ID",
              "FIREBASE_APP_ID"}
    assert needed <= env_keys()


def test_secrets_are_not_in_the_file():
    for env in service()["envVars"]:
        if env["key"] not in ("PYTHON_VERSION",):
            assert "value" not in env, env["key"]
