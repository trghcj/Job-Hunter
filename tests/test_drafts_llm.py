"""Cover letter and resume tips: the prompt, strict reply checking, and the Gemini call."""

import json

import httpx
import pytest

from jobhunter.llm.client import Draft, JobForReview, LLMError, build_draft_prompt, parse_draft
from jobhunter.llm.gemini import GeminiClient

KEY = "AIzaTESTKEY1234567890"
JOB = JobForReview(1, "Backend Engineer", "Acme", "Pune", "Django and REST APIs")


def client(handler) -> GeminiClient:
    return GeminiClient(KEY, "gemini-2.5-flash",
                        http=httpx.Client(transport=httpx.MockTransport(handler)))


def gemini_reply(text: str) -> httpx.Response:
    return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}}]})


def test_prompt_has_profile_and_untrusted_job():
    prompt = build_draft_prompt("Python, FastAPI",
                                JobForReview(1, "Dev", "Acme", "Pune", "x" * 5000))
    assert "Python, FastAPI" in prompt and "untrusted" in prompt
    assert "[Your name]" in prompt and "200" in prompt and "250" in prompt
    assert "x" * 3000 in prompt and "x" * 3001 not in prompt


def test_parse_draft_good():
    raw = '{"letter": " Dear team ", "tips": ["a  b", 3, "", "c"]}'
    assert parse_draft(raw) == Draft("Dear team", ("a b", "c"))


def test_parse_draft_caps():
    raw = json.dumps({"letter": "L" * 4000, "tips": ["t" * 300] + [f"tip {i}" for i in range(6)]})
    draft = parse_draft(raw)
    assert len(draft.letter) == 3000 and len(draft.tips) == 5 and len(draft.tips[0]) == 200


def test_parse_draft_zero_tips_ok():
    assert parse_draft('{"letter": "Hi", "tips": []}') == Draft("Hi", ())


@pytest.mark.parametrize("raw", [
    "not json", "[]", '{"tips": []}', '{"letter": "  ", "tips": []}',
    '{"letter": 5, "tips": []}', '{"letter": "Hi", "tips": "x"}'])
def test_parse_draft_rejects(raw):
    with pytest.raises(LLMError):
        parse_draft(raw)


def test_gemini_draft_ok():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return gemini_reply('{"letter":"Hi","tips":["t"]}')

    assert client(handler).draft("Python", JOB) == Draft("Hi", ("t",))
    config = seen["body"]["generationConfig"]
    assert config["temperature"] == 0.4 and config["responseSchema"]["type"] == "OBJECT"
    assert "Django and REST APIs" in seen["body"]["contents"][0]["parts"][0]["text"]
    assert KEY not in json.dumps(seen["body"])


def test_gemini_draft_http_500():
    with pytest.raises(LLMError, match=r"Gemini answered HTTP 500\.") as err:
        client(lambda r: httpx.Response(500, text=f"boom {KEY}")).draft("Python", JOB)
    assert KEY not in str(err.value)


def test_gemini_draft_timeout():
    def timeout(request):
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(LLMError):
        client(timeout).draft("Python", JOB)
