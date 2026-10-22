# Changelog

## 1.2.1 — reading accuracy from the first laptop run (October 2026)

- **CV (reader cv/2):** PDF text is read in reading order by position. Some CV templates
  draw every heading first and every date last, so the model could not tell which lines
  were projects or which dates went with which entry. Runs at the same height now form one
  line (a date stays with its entry), lines go top to bottom, and a real sidebar column is
  read as its own column. Falls back to the stored order if the placed text loses anything.
- **CV:** `gpa_scale` is kept only when the CV writes a scale ("8.9/10", "out of 10",
  "10-point scale"); the model used to repeat the GPA when the CV gave no scale.
- **LinkedIn (reader linkedin/2):** mobile-app screenshots. The "<Name>'s top skills" page is
  recognised as a skills section (its skills were all rejected before); a page title such as
  "top skills" is never a headline; the school shown on the profile card is kept.

## 1.2.0 — lookup agents: NPPES, OIG, SAM (October 2026)

- Three lookup agents, ported from OneSmarter's kormic-agents package: **NPPES** (a US NPI
  at CMS), **OIG** (HHS-OIG exclusion list) and **SAM** (SAM.gov exclusions). Off by
  default; see [Lookup agents](lookup-agents.md).
- Changes from the original: NPPES no longer compares the given name with the record
  (projects decide; OIG and SAM still match names, only to search their lists); OIG no
  longer says an NPI-only hit matched on the name; SAM reads every page instead of the
  first 50 (also when SAM.gov gives no total) and refuses a partial answer; SAM compares
  each name given on its own (a former name could hide a hit) and never lets an inactive
  copy hide an active record; SAM's daily limit is shared by every worker and answers are
  cached under the exact name sent; the OIG list is loaded once and shared by every worker.
- Shared support for lookup agents: `lookups/http.py` (safe outside calls), `JobFailed`
  with new job error codes, agents' own settings (`AGENTS_<AGENT>_...`), a shared daily
  limit, a cache of outside answers and reference files (migration 0003),
  `agents tool <agent> <command>`, and each agent's state in `/health/ready`.
- httpx's own request logging (full addresses, so any key in them) is switched off.
- Second review: SAM sends only the first and last name words (SAM.gov ANDs every word, so
  a middle name or title could hide a listed person) and pages of 10 (GSA's maximum);
  an optional per-project SAM daily limit; NPPES treats an answer it cannot read as
  `source_unreadable`, never as "no such NPI"; OIG finds listings with only an initial
  or no first name and refuses names it cannot search ("Smith" alone); a corrected OIG
  publication date is picked up; `/health/ready` shows only ok / needs setup (the reason
  is in `GET /v1/agents`); `--data-file` reads UTF-8 (with or without BOM).

## 1.1.0 — fixes from the second review (October 2026)

- Hostile files stay out of the API: the API runs only quick checks (size, type, header,
  the Word file's part list). A PDF's page tree and a Word file's links are checked in the
  worker's limited reader process. A 90,000-page PDF now takes 0.0 s in the API (was 51 s).
- Each read has its own empty folder (working and temp folder), deleted afterwards; on
  timeout the reader and everything it started (tesseract) are killed together.
- Each agent's upload limit applies to its own address only.
- A database error while saving no longer marks a good result failed; the job returns to
  the line if it has attempts left. A retried job gets a fresh wait (new column `queued_at`,
  migration 0002; jobs from a 1.0 server during an upgrade use `created_at`).
- The sweep never deletes a waiting or running job. Results are kept 7 days after the job
  ends (was: after it was created). A job's input data is deleted when it ends, and the
  stored request fingerprint is keyed, so it cannot be used to guess that data.
- A relative `AGENTS_TESSERACT_CMD` is made absolute for the reader process.
- Fair turns count only the agents a worker serves. Running jobs check the line every 10 s.
- Job line settings that would break it are refused at start.
- Model overrides that change the provider or server get that provider's limits and never
  the global API key. `reasoning_effort` is sent only when set (Ollama keeps `none`).
- Lookup (data) agents: `empty_is_answer`, `data_record()`, `job.check_cancelled()`, a working
  template (`agents/_template_data`), `agents try --data`, and `data_schema` in `GET /v1/agents`.
- Agent definitions and record shapes are checked.
- Renamed to Universal Agents (package `universal-agents`).

## 1.0.0 — phase 1 (October 2026)

- New layered service `agents_service` with an agent registry; the October pilot moved to `legacy/`.
- Agents: CV (PDF, Word) and LinkedIn screenshots, using Kormic Careers' tested readers.
- Keys belong to one project and one agent; hashed, revocable, audited.
- One-call jobs at `/v1/<agent>/jobs` with `Idempotency-Key`, `fields` and `subject_ref`.
- Database job line: renewed borrows, fair turns between projects, expiry, retries; works with
  many workers on many machines.
- Storage on local disk or S3; SQLite or PostgreSQL; Alembic migrations.
- Model: Ollama/OpenAI-compatible (default) or Anthropic, per agent.
- Speed settings for CPU models: bigger CV pieces and text-first prompts.
- Results carry `reader_version`, `model` and `timing`; usage per project and agent (`agents stats`).
- `agents try`, `agents accuracy`, `agents check`; runs on Windows and Linux.
- Independent review fixes: a worker that lost its job never deletes the new holder's files;
  counters use single-statement upserts (safe on PostgreSQL); the worker survives save failures;
  Ctrl+C no longer kills a file being read; S3 cleanup touches only job folders; uploads are
  counted, not buffered, so keys are checked first; job checks have their own limit; the queue
  position follows fair turns; one request id per response; `AGENTS_FILE_SECONDS` bounds reading.
- The whole test suite also passes on PostgreSQL 16.
