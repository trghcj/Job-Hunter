"""What a user told JobHunter to show less of, from "Not interested" reasons.

Stored per user (user_settings key "avoid"): companies and cities are hidden by the filters;
skills and role titles lower the fit score (pipeline, matching/fit.py). Every entry is shown
on Settings and can be removed.
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from jobhunter.db import Job
from jobhunter.normalize import company_key
from jobhunter.user_settings import get_user_setting, set_user_setting

REASONS = {
    "company": "Don't like this company",
    "skills": "Wrong skills",
    "location": "Wrong location",
    "role": "Not my kind of role",
}
KINDS = ("companies", "skills", "cities", "titles")
MAX_ENTRIES = 100


@dataclass(frozen=True)
class Avoid:
    companies: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    cities: tuple[str, ...] = ()
    titles: tuple[str, ...] = ()

    def is_empty(self) -> bool:
        return not (self.companies or self.skills or self.cities or self.titles)

    def avoids_company(self, company: str) -> bool:
        key = company_key(company or "")
        return bool(key) and any(company_key(c) == key for c in self.companies)

    def avoided_city(self, city: str | None, work_mode: str | None) -> str | None:
        """The avoided city this job is in, or None (remote jobs are never affected)."""
        if not city or work_mode == "remote":
            return None
        return next((c for c in self.cities if c.lower() == city.strip().lower()), None)


def load_avoid(session: Session, user_id: int) -> Avoid:
    value = get_user_setting(session, user_id, "avoid") or {}
    if not isinstance(value, dict):
        return Avoid()
    return Avoid(*(tuple(str(v) for v in value.get(kind) or ()) for kind in KINDS))


def _store(session: Session, user_id: int, avoid: Avoid) -> None:
    set_user_setting(session, user_id, "avoid",
                     {kind: list(getattr(avoid, kind)) for kind in KINDS})


def _missing_skills(job: Job, profile_skills: set[str]) -> list[str]:
    return sorted(set(job.skills or ()) - set(profile_skills), key=str.lower)


def reason_options(job: Job, profile_skills: set[str]) -> list[str]:
    """The reasons that would change something for this job, in REASONS order."""
    useful = {
        "company": bool(job.company),
        "skills": bool(_missing_skills(job, profile_skills)),
        "location": bool(job.city) and job.work_mode != "remote",
        "role": bool(job.title),
    }
    return [reason for reason in REASONS if useful[reason]]


def _added(current: tuple[str, ...], new: list[str], same) -> tuple[str, ...]:
    out = list(current)
    for value in new:
        if len(out) >= MAX_ENTRIES:
            break
        if value and not any(same(value, existing) for existing in out):
            out.append(value)
    return tuple(out)


def _lower_equal(a: str, b: str) -> bool:
    return a.lower() == b.lower()


def add_reason(session: Session, user_id: int, job: Job, reason: str,
               profile_skills: set[str]) -> bool:
    """Record why the job was not wanted. False (nothing changed) when the reason adds nothing."""
    if reason not in reason_options(job, profile_skills):
        return False
    avoid = load_avoid(session, user_id)
    if reason == "company":
        changed = Avoid(_added(avoid.companies, [job.company],
                               lambda a, b: company_key(a) == company_key(b)),
                        avoid.skills, avoid.cities, avoid.titles)
    elif reason == "skills":
        changed = Avoid(avoid.companies,
                        _added(avoid.skills, _missing_skills(job, profile_skills), _lower_equal),
                        avoid.cities, avoid.titles)
    elif reason == "location":
        changed = Avoid(avoid.companies, avoid.skills,
                        _added(avoid.cities, [job.city.strip().title()], _lower_equal),
                        avoid.titles)
    else:
        changed = Avoid(avoid.companies, avoid.skills, avoid.cities,
                        _added(avoid.titles, [job.title.strip()], _lower_equal))
    if changed == avoid:
        return False
    _store(session, user_id, changed)
    return True


def remove_entry(session: Session, user_id: int, kind: str, value: str) -> bool:
    if kind not in KINDS:
        return False
    avoid = load_avoid(session, user_id)
    current = getattr(avoid, kind)
    kept = tuple(v for v in current if v.lower() != value.strip().lower())
    if kept == current:
        return False
    _store(session, user_id, Avoid(**{**{k: getattr(avoid, k) for k in KINDS}, kind: kept}))
    return True
