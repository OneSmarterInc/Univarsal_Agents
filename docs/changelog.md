# Changelog

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
