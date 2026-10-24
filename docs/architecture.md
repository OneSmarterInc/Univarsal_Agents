# Architecture

## What it does

```
Project backend ──(agent key)──► API ──► job line (database) ──► worker ──► model (Qwen)
       ▲                                                            │
       └──────────── JSON: records with exact quotes ◄──────────────┘
```

1. A project sends files to `POST /v1/<agent>/jobs`. The API checks the key, checks the
   files, stores them, queues the job and answers at once with a job id.
2. A worker borrows the next job (projects take turns), reads the files, asks the model,
   keeps only facts quoted from the file, saves the result and deletes the files.
3. The project reads `GET /v1/<agent>/jobs/{id}` until the job is finished.

## Layers

Each layer uses only the layers below it. `tests/architecture/test_layers.py` checks this.

```
1. Entry points   api/  worker/  cli/            how requests come in
2. Agents         agents/cv  agents/linkedin  …  one folder per agent
3. Shared tools   ai/  documents/  lookups/      model calls; file reading, OCR, safety; outside sources
4. Core           core/                          keys, jobs, storage, limits, usage, config, db
```

Rules: an agent never imports another agent; agents never import `api/` or `worker/`;
only `ai/` talks to a model; agents call outside sources only through `lookups/http.py`;
only `core/storage.py` touches disk or S3.
`agents_service/services.py` wires the layers together.

## Folders

| Folder | Holds |
|---|---|
| `api/` | `app.py` (routes built from the agent registry), `middleware.py` (request id, browser refusal, size limit, safe errors), `inputs.py` |
| `agents/` | `base.py` (the agent contract), `registry.py` (the list), `records.py` (shared record building), one folder per agent, `_template/` (file agent) and `_template_data/` (lookup agent) |
| `ai/` | `providers.py` (Ollama/OpenAI-compatible and Anthropic, timing, cancellation), `schema.py` |
| `lookups/` | `http.py` (outside calls: HTTPS, time and size limits, no keys in logs), `screening.py` (list-search rules), `values.py` |
| `documents/` | `reading.py` (PDF, Word, OCR), `isolation.py` (separate limited process), `safety.py` (upload checks), `matching.py`, `text.py` |
| `core/` | `config.py`, `db.py`, `models.py`, `keys.py`, `jobs.py`, `storage.py`, `limits.py` (also daily limits), `cache.py` (outside answers), `references.py` (agents' reference files), `usage.py`, `audit.py`, `errors.py` |
| `worker/` | `runner.py` (borrow, run, renew, save) |
| `cli/` | `main.py` (the `agents` command), `accuracy.py` |
| `migrations/` | Database upgrades (Alembic) |

## The job line

- Jobs live in the `jobs` table. A worker takes one with a conditional update on that
  row only, so any number of workers on any number of machines can share the line.
- The worker "borrows" a job for `AGENTS_LEASE_SECONDS` and renews it while working.
  A dead worker's job returns to the line once, then fails as `worker_lost`.
- **Fair turns:** the next job comes from the project whose last job started longest ago.
- Waiting jobs older than `AGENTS_QUEUE_EXPIRY_SECONDS` become `expired`. Results are
  deleted after `AGENTS_RESULT_RETENTION_SECONDS`. Workers run this housekeeping every minute.
- If the model cannot be reached, the job goes back to the line once, then fails as
  `model_unavailable`. A cancelled job stops before its next model question.
- A lookup agent ends a job with its own code (`JobFailed`); an outside source that did not
  answer gets one more try, then `source_unavailable`.

## Growth

| Need | Change (no code change) |
|---|---|
| More throughput | Start more workers, on the same or more machines |
| Several machines | `AGENTS_STORAGE=s3` and a PostgreSQL `AGENTS_DATABASE_URL` |
| Faster model | Qwen on a GPU with vLLM (`AGENTS_MODEL_BASE_URL`) |
| One busy agent | Workers just for it: `agents worker --agents cv` |
| An API-key model | `AGENTS_MODEL_PROVIDER=anthropic`, for all or some agents |

## Safety and privacy

- Keys are stored as HMAC hashes, belong to one project and one agent, and are audited.
- Uploads are checked by content (PDF header, Word archive rules, image decoding) before storage.
- Each file is read in a separate process with a time limit and, on Linux, memory and CPU limits.
- Every fact must be an exact quote from the file, or it is dropped.
- No file content is logged. Files and input data are deleted when the job ends; results 7 days after it ends.
- S3 files are encrypted at rest (SSE-S3). Use HTTPS in front of the API on servers.
