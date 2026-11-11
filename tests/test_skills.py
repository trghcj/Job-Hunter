"""Skill extraction: canonical names from aliases, whole words only, no common-word false hits."""

from jobhunter.matching.skills import SKILLS, extract_skills


def test_aliases_map_to_canonical():
    assert extract_skills("JS, Postgres and k8s") == {"JavaScript", "PostgreSQL", "Kubernetes"}


def test_java_not_inside_javascript():
    assert extract_skills("Strong JavaScript") == {"JavaScript"}
    assert extract_skills("Java and JavaScript") == {"Java", "JavaScript"}


def test_symbols():
    assert extract_skills("C++, C#, .NET Core, Node.js") >= {"C++", "C#", ".NET", "Node.js"}


def test_common_words_are_not_skills():
    assert extract_skills("Let's go! R&D team, spring hiring, swift response") == set()
    assert extract_skills("Golang and Spring Boot, Swift (iOS)") == {"Go", "Spring Boot", "Swift"}


def test_case_insensitive_and_empty():
    assert extract_skills("PYTHON django") == {"Python", "Django"}
    assert extract_skills(None) == set()
    assert extract_skills("") == set()


def test_table_size_and_unique_aliases():
    assert len(SKILLS) >= 250
    aliases = [a.lower() for names in SKILLS.values() for a in names]
    assert len(aliases) == len(set(aliases))


def test_sentence_punctuation_after_skill():
    assert extract_skills("We use Node.js. Also React.") == {"Node.js", "React"}


def test_rest_as_a_word_is_not_rest_apis():
    assert extract_skills("The rest of the team") == set()
    assert "REST APIs" in extract_skills("Build REST APIs and RESTful services")
