"""How well a job that passed the filters fits a user's profile (0..1), its level and reason."""

from dataclasses import dataclass
from typing import Literal

Level = Literal["strong", "good", "weak"]

SKILL_WEIGHT = 0.65
TITLE_WEIGHT = 0.35
NO_SKILLS_PART = 0.5  # a job that names no skills we know is neither good nor bad
EXPERIENCE_UNSTATED_PENALTY = 0.1
REMOTE_UNCONFIRMED_PENALTY = 0.1
REASON_NAMES = 4
LEVEL_MIN = {"strong": 0.75, "good": 0.55, "all": 0.0}  # also the digest's fit choices
NO_SKILLS_REASON = "Title matches; job lists no specific skills"
AVOID_SKILL_PENALTY = 0.1  # per avoided skill in the job ("Not interested: wrong skills")
AVOID_SKILLS_MAX = 0.3
REJECTED_ROLE_PENALTY = 0.2  # title like one marked "Not my kind of role"
AVOID_NAMES = 3


@dataclass(frozen=True)
class Fit:
    score: float
    skill: float
    level: Level
    matched: tuple[str, ...]
    missing: tuple[str, ...]
    reason: str


def level_of(score: float) -> Level:
    if score >= LEVEL_MIN["strong"]:
        return "strong"
    return "good" if score >= LEVEL_MIN["good"] else "weak"


def blend(local: float, llm_fit: int) -> float:
    """Final score once Gemini has rated the job 1-5."""
    return round(0.5 * local + 0.5 * (llm_fit - 1) / 4, 4)


def _ordered(names: set[str], title_skills: set[str]) -> tuple[str, ...]:
    return tuple(sorted(names, key=lambda n: (n not in title_skills, n.lower())))


def _reason(matched: tuple[str, ...], missing: tuple[str, ...]) -> str:
    parts = []
    if matched:
        parts.append("Matches " + ", ".join(matched[:REASON_NAMES]))
    if missing:
        parts.append("Missing " + ", ".join(missing[:REASON_NAMES]))
    return " · ".join(parts)


def _lowered(avoided: tuple[str, ...], rejected_role: bool) -> str:
    notes = []
    if avoided:
        notes.append("you avoid " + ", ".join(avoided[:AVOID_NAMES]))
    if rejected_role:
        notes.append("role like one you rejected")
    return f" · Lowered: {'; '.join(notes)}" if notes else ""


def local_fit(job_skills: set[str], title_skills: set[str], profile_skills: set[str],
              title_score: float, experience_unstated: bool, remote_unconfirmed: bool,
              avoid_skills: set[str] = frozenset(), rejected_role: bool = False) -> Fit:
    title_skills = title_skills & job_skills
    if job_skills:
        weight = {s: 2 if s in title_skills else 1 for s in job_skills}
        skill = sum(weight[s] for s in job_skills & profile_skills) / sum(weight.values())
    else:
        skill = NO_SKILLS_PART
    score = SKILL_WEIGHT * skill + TITLE_WEIGHT * title_score
    score -= EXPERIENCE_UNSTATED_PENALTY if experience_unstated else 0
    score -= REMOTE_UNCONFIRMED_PENALTY if remote_unconfirmed else 0
    avoided = _ordered(job_skills & set(avoid_skills), title_skills)
    score -= min(AVOID_SKILL_PENALTY * len(avoided), AVOID_SKILLS_MAX)
    score -= REJECTED_ROLE_PENALTY if rejected_role else 0
    score = round(min(max(score, 0.0), 1.0), 4)
    matched = _ordered(job_skills & profile_skills, title_skills)
    missing = _ordered(job_skills - profile_skills, title_skills)
    base = _reason(matched, missing) if job_skills else NO_SKILLS_REASON
    reason = base + _lowered(avoided, rejected_role)
    return Fit(score, round(skill, 4), level_of(score), matched, missing, reason)
