# Phase 4: Fit scoring from a resume (design)

Status: approved in conversation 2026-11-12, waiting for written-spec review.

## Goal

Rank the jobs that pass a user's filters by how well they fit that user's resume, say why in
one line, and let each user keep only good fits in the daily email.

Success for the user:
- **Best jobs on top**: the Matches tab, the digest and the extension bubble are ordered by fit.
- **Why it fits**: every passing job carries a short reason ("Matches Python, Django, SQL ·
  Missing AWS").
- **Fewer, better emails**: each user picks a fit level for the digest.

The filters keep deciding pass/fail. Scoring never hides a job from the Matches tab.

## Decisions (from the user)

| Topic | Decision |
|---|---|
| Profile source | Upload a resume PDF; only cleaned text is kept, editable |
| Fit measure | Local skill matching for every job (approach A) |
| AI | Gemini reviews each user's top 10 new jobs per day (hybrid); everything works without it |
| AI consent | Per-user checkbox, **off by default** |
| Digest | Per-user fit level: Strong only / Good and strong (default) / Everything that passed |

Not in this phase: embeddings (approach B/C), resume or cover-letter drafts (Phase 6),
feedback from Applied/Not interested (Phase 5).

## 1. Profile (resume upload)

- Settings gets a **My profile** card: *Upload resume (PDF)*, an editable text box with *Save*,
  the detected skills shown as tags, and the AI checkbox (section 3).
- Upload: PDF only, at most 2 MB and 10 pages. Text is extracted with `pypdf` (added to the
  `web` extra). The PDF is never stored or logged.
- Cleaning (`profile.clean_text`) removes email addresses, phone numbers (Indian and
  international formats) and URLs, collapses whitespace, and cuts to 15,000 characters.
  The user can edit the result.
- A PDF with no extractable text (a scan) shows: "Couldn't read text from this PDF, paste
  your resume text instead." The text box always accepts pasted text.
- Stored per user in `user_settings` under `profile` = `{text, skills, updated_at}`.
  Only that user sees it. Skills are recomputed on every save.
- Without a profile, scoring works as today and the Matches tab shows "Upload your resume
  to rank jobs by fit."

## 2. Local fit score (every job, free, no network)

- `matching/skills.py`: a data table of about 300 tech skills, each with a canonical name and
  aliases (JS → JavaScript, Postgres → PostgreSQL, k8s → Kubernetes, .NET, C++, C#, Node.js).
  `extract_skills(text) -> set[str]` matches whole tokens, case-insensitive, so "Java" never
  matches inside "JavaScript".
- Job skills are extracted once at upsert from title + description and stored on the job
  (`jobs.skills`, shared by all users; title skills are kept separately as `jobs.title_skills`).
- `matching/fit.py`: `fit(job, title_score, profile_skills, penalties) -> Fit(score, level,
  matched, missing, reason)`:
  - skill part = matched job skills / job skills, with title skills counted twice;
    a job with no recognised skills gets 0.5 (neutral);
  - score = 0.65 × skill part + 0.35 × title score − existing penalties (0.1 for unstated
    experience, 0.1 for remote roles that never mention India), clamped to 0..1;
  - level: **strong** ≥ 0.75, **good** ≥ 0.55, else **weak**;
  - reason: "Matches A, B, C · Missing D, E" (at most 4 each), or "Title matches; job lists no
    specific skills".
- Stored in `job_scores` (`skill_score`, `final_score`, new `fit_reason`).
- Rescoring: saving the profile or titles re-scores that user's jobs at once (extends the
  existing `refilter_for_user`). New jobs are scored in the daily run.
- Shown in: Matches tab (sorted by score, level badge + reason), digest email (badge + reason
  under each job), extension bubble ("Strong fit · Python, Django match"; the API's check
  response gains `fit: {level, score, reason}`; the old verdict field stays).

## 3. Gemini review (top 10 per user per day)

- Runs in the daily run after local scoring and before the digest, only for users with a
  profile **and** the checkbox "Let Gemini review my top 10 jobs each day" turned on
  (default off; the checkbox explains that profile text without contact details is sent to
  Google).
- Per user: the 10 best-scoring passing jobs with no review yet and status new/saved, sent in
  **one** call. Daily calls are counted in `llm_usage`; the existing `llm.max_calls_per_run`
  (30) caps a run.
- `llm/client.py`: `LLMClient` protocol, `review(profile_text, jobs) -> list[Review]`;
  `llm/gemini.py`: Gemini REST `generateContent` over httpx with a JSON response schema.
  Model from settings (`GEMINI_MODEL`, default `gemini-2.5-flash`); key `GEMINI_API_KEY`.
- Sent: the profile text with its first line (usually the name) dropped, after cleaning; per
  job: title, company, location, description cut to 3,000 characters. Nothing else.
- Received per job: `fit` 1–5 and `reason` ≤ 140 characters. Anything else, a missing job, or
  an out-of-range value is ignored for that job.
- Stored in `llm_fit`, `llm_json`; the review reason replaces `fit_reason`;
  `final_score = 0.5 × local score + 0.5 × (fit − 1) / 4`, then the level is recomputed.
- Saving the profile clears the user's reviews so they are redone.
- Failures (no key, network, quota, bad JSON) are caught, logged to `runs` as `llm:<user_id>`,
  local scores stay, the run continues. Job descriptions are untrusted: the model can only
  return a number and a short text, which is validated and always shown as plain text.

## 4. Digest

- New per-user setting `digest_fit`: `strong` | `good` (default) | `all`, edited on Settings.
- `select_digest` keeps only passing jobs at or above that level, best first, still capped by
  `digest.max_jobs`. The shared `min_final_score` app setting is retired.
- A day with nothing at the chosen level sends no digest (the existing "no jobs" path).

## Data changes (migration 0007)

- `jobs.skills TEXT[]`, `jobs.title_skills TEXT[]` (filled for existing jobs by the migration's
  backfill command `python -m jobhunter rescore`, not inside the migration).
- `job_scores.fit_reason TEXT`.
- `user_settings` keys added to `USER_KEYS`: `profile`, `llm_review` (bool, default false),
  `digest_fit` (default `good`).

## Settings and secrets

- `GEMINI_API_KEY` (secret) and `GEMINI_MODEL` in `settings.py`; set in `.env` and GitHub
  Actions secrets. Render does not need them (the site and bubble use only local scoring).

## Privacy and safety

- The PDF is never stored. Contact details are removed before storage; the first line is also
  removed before anything goes to Gemini.
- Profiles are per user and filtered by `user_id` like every per-user table.
- Gemini is off unless the user turns it on.

## Testing

- No network: a fake `LLMClient`; Gemini request/response tested against saved JSON fixtures.
- A synthetic resume PDF fixture (made-up person) for extraction and cleaning.
- Tests: skill extraction (aliases, "Java" vs "JavaScript", C++/C#/.NET); fit formula and
  levels; reason text; cleaning removes emails/phones/URLs; first line never reaches the
  prompt; review only for opted-in users, max 10 jobs, one call; failures keep local scores
  and log a run row; invalid review values ignored; digest respects `digest_fit`; profile
  save re-scores and clears reviews; upload limits (size, pages, non-PDF, no text);
  per-user isolation of profiles; extension check returns `fit`; bubble shows level + reason.
