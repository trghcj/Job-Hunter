from jobhunter.settings import Settings
from jobhunter.sources.registry import build_adapters
from jobhunter.sources.watchlist import Company

WATCHLIST = [
    Company("Groww", "greenhouse", "groww"),
    Company("CRED", "lever", "cred"),
    Company("Paytm", "lever", "paytm"),
    Company("Atlan", "ashby", "atlan"),
]
NO_KEYS = Settings(adzuna_app_id="", adzuna_app_key="")


def names(adapters):
    return [a.name for a in adapters]


def test_new_sources_are_on_by_default():
    adapters = build_adapters(None, NO_KEYS, {}, WATCHLIST)
    assert names(adapters) == ["greenhouse", "lever", "ashby", "remotive", "remoteok"]
    by_name = {a.name: a for a in adapters}
    assert [c.slug for c in by_name["lever"].companies] == ["cred", "paytm"]
    assert [c.slug for c in by_name["greenhouse"].companies] == ["groww"]


def test_sources_can_be_switched_off():
    sources = {"remotive": {"enabled": False}, "lever": {"enabled": False}}
    assert names(build_adapters(None, NO_KEYS, sources, WATCHLIST)) == [
        "greenhouse", "ashby", "remoteok"
    ]


def test_board_without_companies_is_skipped():
    only_lever = [Company("CRED", "lever", "cred")]
    assert "greenhouse" not in names(build_adapters(None, NO_KEYS, {}, only_lever))


def test_adzuna_needs_keys():
    assert "adzuna" not in names(build_adapters(None, NO_KEYS, {"adzuna": {"enabled": True}}, []))
