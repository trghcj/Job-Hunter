"""Google Gemini (free tier) as an LLMClient, over its REST API."""

import httpx

from jobhunter.llm.client import (
    Draft,
    JobForReview,
    LLMError,
    Review,
    build_draft_prompt,
    build_prompt,
    parse_draft,
    parse_reviews,
)

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
DRAFT_SCHEMA = {
    "type": "OBJECT",
    "properties": {"letter": {"type": "STRING"},
                   "tips": {"type": "ARRAY", "items": {"type": "STRING"}}},
    "required": ["letter", "tips"],
}


class GeminiClient:
    provider = "gemini"

    def __init__(self, api_key: str, model: str, http: httpx.Client | None = None,
                 timeout: float = 60) -> None:
        self._key, self.model = api_key, model
        self._http = http or httpx.Client(timeout=timeout)

    def review(self, profile_text: str, jobs: list[JobForReview]) -> list[Review]:
        text = self._generate(build_prompt(profile_text, jobs), SCHEMA, 0.2)
        return parse_reviews(text, {job.id for job in jobs})

    def draft(self, profile_text: str, job: JobForReview) -> Draft:
        return parse_draft(self._generate(build_draft_prompt(profile_text, job),
                                          DRAFT_SCHEMA, 0.4))

    def _generate(self, prompt: str, schema: dict, temperature: float) -> str:
        """The model's JSON answer as text. Raises LLMError without secrets or reply bodies."""
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json",
                                 "responseSchema": schema, "temperature": temperature},
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
            return response.json()["candidates"][0]["content"]["parts"][0]["text"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError("Gemini's answer had an unexpected shape.") from exc
