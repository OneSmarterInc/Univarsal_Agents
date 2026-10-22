# Changelog

## 1.1.0 — fixes from the second review (October 2026)

- Hostile files stay out of the API: the API runs only quick checks (size, type, header,
  the Word file's part list). A PDF's page tree and a Word file's links are checked in the
  worker's limited reader process. A 90,000-page PDF now takes 0.0 s in the API (was 51 s).
- Each read has its own empty folder (working and temp folder), deleted afterwards; on
  timeout the reader and everything it started (tesseract) are killed together.
- Each agent's upload limit applies to its own address only.
- A database error while saving no longer marks a good result failed; the job returns to
  the line. A retried job gets a fresh wait (new column `queued_at`, migration 0002).
- The sweep never deletes a waiting or running job. A job's input data is deleted when it ends.
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
