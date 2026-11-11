"""Local fit score: skill overlap (title skills count twice), title score, penalties, levels."""

from jobhunter.matching.fit import LEVEL_MIN, blend, level_of, local_fit


def test_all_skills_matched_is_strong():
    f = local_fit({"Python", "Django"}, set(), {"Python", "Django", "SQL"}, 1.0, False, False)
    assert f.score == 1.0 and f.level == "strong" and f.reason == "Matches Django, Python"


def test_title_skills_count_double():
    f = local_fit({"React", "Java"}, {"React"}, {"React"}, 1.0, False, False)
    assert f.skill == round(2 / 3, 4)


def test_no_job_skills_is_neutral():
    f = local_fit(set(), set(), {"Python"}, 1.0, False, False)
    assert f.skill == 0.5 and f.reason == "Title matches; job lists no specific skills"
    assert f.score == round(0.65 * 0.5 + 0.35, 4)


def test_penalties_and_clamp():
    f = local_fit({"AWS"}, set(), set(), 0.0, True, True)
    assert f.score == 0.0 and f.level == "weak"


def test_penalty_amounts():
    f = local_fit({"Python"}, set(), {"Python"}, 1.0, True, False)
    assert f.score == 0.9


def test_reason_lists_at_most_four_each_matched_first():
    job = {"Python", "Django", "SQL", "Redis", "AWS", "Docker", "Kafka", "Go", "Rust"}
    f = local_fit(job, set(), {"Python", "Django", "SQL", "Redis", "Docker"}, 1.0, False, False)
    assert f.reason.startswith("Matches ") and " · Missing " in f.reason
    assert len(f.matched) == 5 and f.reason.count(",") == 3 + 3


def test_only_missing():
    f = local_fit({"AWS", "Go"}, set(), {"Python"}, 1.0, False, False)
    assert f.reason == "Missing AWS, Go"


def test_title_skills_named_first():
    f = local_fit({"AWS", "React", "CSS"}, {"React"}, {"React", "CSS", "AWS"}, 1.0, False, False)
    assert f.reason == "Matches React, AWS, CSS"


def test_levels_and_blend():
    assert [level_of(s) for s in (0.75, 0.7499, 0.55, 0.5499)] == ["strong", "good", "good", "weak"]
    assert blend(0.6, 5) == 0.8 and blend(0.6, 1) == 0.3
    assert LEVEL_MIN == {"strong": 0.75, "good": 0.55, "all": 0.0}
