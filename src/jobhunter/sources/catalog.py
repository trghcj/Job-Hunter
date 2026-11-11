"""Names of the job sources, shared by the registry and the settings page."""

from typing import Any

SOURCE_LABELS = {
    "adzuna": "Adzuna India",
    "greenhouse": "Greenhouse careers pages",
    "lever": "Lever careers pages",
    "ashby": "Ashby careers pages",
    "remotive": "Remotive (remote)",
    "remoteok": "Remote OK (remote)",
}
DEFAULT_ENABLED = {name: True for name in SOURCE_LABELS}


def is_enabled(sources: dict[str, Any], name: str) -> bool:
    cfg = sources.get(name) or {}
    return bool(cfg.get("enabled", DEFAULT_ENABLED.get(name, False)))
