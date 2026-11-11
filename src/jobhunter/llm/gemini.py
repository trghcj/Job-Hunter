"""Google Gemini (free tier) as an LLMClient, over its REST API."""

import httpx

from jobhunter.llm.client import JobForReview, LLMError, Review, build_prompt, parse_reviews

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {"id": {"type": "INTEGER"}, "fit": {"type": "INTEGER"},
                       "reason": {"type": "STRING"}},
        "required": ["id", "fit", "reason"],
    },
}


class GeminiClient:
    provider = "gemini"

    def __init__(self, api_key: str, model: str, http: httpx.Client | None = None,
                 timeout: float = 60) -> None:
        self._key, self.model = api_key, model
        self._http = http or httpx.Client(timeout=timeout)

    def review(self, profile_text: str, jobs: list[JobForReview]) -> list[Review]:
        body = {
            "contents": [{"role": "user",
                          "parts": [{"text": build_prompt(profile_text, jobs)}]}],
            "generationConfig": {"responseMimeType": "application/json",
                                 "responseSchema": SCHEMA, "temperature": 0.2},
        }
        try:
            response = self._http.post(API.format(model=self.model), json=body,
                                       headers={"x-goog-api-key": self._key})
        except httpx.TimeoutException as exc:
            raise LLMError("Gemini did not answer in time.") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Could not reach Gemini ({type(exc).__name__}).") from exc
        if response.status_code != 200:
            # The body is not included: it is not ours and could echo request details.
            raise LLMError(f"Gemini answered HTTP {response.status_code}.")
        try:
            text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError("Gemini's answer had an unexpected shape.") from exc
        return parse_reviews(text, {job.id for job in jobs})
