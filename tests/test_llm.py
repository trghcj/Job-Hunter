"""LLM client: prompt contents, Gemini request/response (no network), strict reply checks."""

import json
from pathlib import Path

import httpx
import pytest

from jobhunter.llm.client import JobForReview, LLMError, build_prompt, parse_reviews
from jobhunter.llm.gemini import GeminiClient

FIXTURES = Path(__file__).parent / "fixtures"
KEY = "AIzaTESTKEY1234567890"
JOBS = [JobForReview(1, "Backend Engineer", "Acme", "Pune", "Django and REST APIs"),
        JobForReview(2, "Java Developer", "Beta", None, "Spring Boot")]


def client(handler) -> GeminiClient:
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return GeminiClient(KEY, "gemini-2.5-flash", http=http)


def test_gemini_request_shape():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["headers"] = str(request.url), request.headers
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, text=(FIXTURES / "gemini_ok.json").read_text())

    client(handler).review("Python, Django", JOBS)
    assert seen["url"] == ("https://generativelanguage.googleapis.com/v1beta/models/"
                           "gemini-2.5-flash:generateContent")
    assert seen["headers"]["x-goog-api-key"] == KEY
    body = seen["body"]
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert body["generationConfig"]["responseSchema"]["type"] == "ARRAY"
    prompt = body["contents"][0]["parts"][0]["text"]
    assert "Backend Engineer" in prompt and "Python, Django" in prompt
    assert KEY not in json.dumps(body)


def test_gemini_parses_fixture():
    reviews = client(lambda r: httpx.Response(
        200, text=(FIXTURES / "gemini_ok.json").read_text())).review("Python", JOBS)
    assert [(r.job_id, r.fit) for r in reviews] == [(1, 4), (2, 2)]
    assert reviews[0].reason.startswith("Strong Django")


def test_gemini_non_json_reply_raises():
    with pytest.raises(LLMError):
        client(lambda r: httpx.Response(
            200, text=(FIXTURES / "gemini_bad.json").read_text())).review("Python", JOBS)


def test_parse_reviews_rejects_bad_values():
    raw = json.dumps([
        {"id": 1, "fit": 0, "reason": "zero"}, {"id": 1, "fit": 6, "reason": "six"},
        {"id": 1, "fit": "5", "reason": "string"}, {"id": 999, "fit": 3, "reason": "unknown"},
        {"id": 2, "fit": 5, "reason": "x" * 500}, {"id": 1, "fit": 3, "reason": 7},
        {"id": 1, "fit": True, "reason": "bool"}, {"id": 1, "fit": 3, "reason": "  ok  "},
    ])
    reviews = parse_reviews(raw, {1, 2})
    assert [(r.job_id, r.fit, len(r.reason)) for r in reviews] == [(2, 5, 140), (1, 3, 2)]
    with pytest.raises(LLMError):
        parse_reviews('{"id": 1}', {1})
    with pytest.raises(LLMError):
        parse_reviews("not json", {1})


def test_http_error_raises_llmerror_without_key():
    with pytest.raises(LLMError) as err:
        client(lambda r: httpx.Response(500, text=f"boom {KEY}")).review("Python", JOBS)
    assert KEY not in str(err.value)

    def timeout(request):
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(LLMError):
        client(timeout).review("Python", JOBS)


def test_prompt_cuts_description_to_3000():
    long = JobForReview(1, "T", "C", None, "a" * 2999 + "b" + "Z" * 20000)
    prompt = build_prompt("Python", [long])
    assert "a" * 2999 + "b" in prompt and "Z" not in prompt
    assert "untrusted" in prompt.lower()
