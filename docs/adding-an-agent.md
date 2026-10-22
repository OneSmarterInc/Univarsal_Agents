# Adding an agent

An agent is one folder. The API addresses, keys, limits, job line, usage and
`GET /v1/agents` come from its definition; nothing else changes.

## Two kinds of agent

| Kind | Input | Template | Example |
|---|---|---|---|
| File agent | Uploaded files (multipart) | `agents/_template/` | CV, LinkedIn |
| Lookup (data) agent | JSON `{"data": {...}}` | `agents/_template_data/` | A register or list lookup |

## Steps

1. **Copy the template** that fits

   ```
   agents_service/agents/_template/       ->  agents_service/agents/certificate/
   agents_service/agents/_template_data/  ->  agents_service/agents/npi_lookup/
   ```

2. **Edit `agent.py`**

   | Field | Example |
   |---|---|
   | `name` | `certificate` (also the folder and the address `/v1/certificate/jobs`); lower case, digits, `_` |
   | `title`, `description` | Shown in `GET /v1/agents` |
   | `key_prefix` | `cert` (keys look like `ua_cert_...`); 1-10 lower case letters or digits |
   | `input_kind` | `files`, or `data` with a pydantic `data_model` for JSON input |
   | `file_rule()` | Accepted endings, media types, number of files, size |
   | `sections` | Every section a record can have |
   | `reader_version` | `certificate/1`; change it whenever the reading changes |
   | `uses_model` | `True` if `run()` asks a model |
   | `empty_is_answer` | `True` for lookups, where "nothing found" is a normal answer (job ends `done`) |
   | `run(job)` | Reads `job.files` (or `job.data`) and returns `AgentResult(records, files, warnings)` |

   Use the shared tools: `read_text()` for files, `provider(job.model)` for model questions,
   `add_records()` / `read_files()` for file records, and keep an exact quote as `evidence`
   for every fact.

   **Lookup agents** build each record with `data_record(section, details, source, retrieved_at)`.
   `source` is a public address (never one carrying a key); `retrieved_at` is when the data
   was fetched, or the publication date of a downloaded list. Call `job.check_cancelled()`
   between outside requests. The job's input data is deleted when the job ends; only the
   result is kept. The NPPES, OIG and SAM agents are full examples.

   | Need | Use |
   |---|---|
   | Call an outside source | `lookups.http.get_json(url, params=..., seconds=..., source=...)`: HTTPS only, time and size limits, keys never logged. Agents may not import httpx themselves |
   | End the job with a reason | `raise JobFailed('source_unavailable', retry=True)` (one more try), or another code from `JOB_ERRORS` in `core/jobs.py` |
   | The agent's own settings | `settings_model = MySettings` (pydantic). Read from `AGENTS_<AGENT>_<FIELD>`; given to the job as `job.agent_settings`. Missing ones fail jobs as `not_configured`. Use `SecretStr` for keys |
   | A daily limit of the source | `job.use_quota('search', limit)`: shared by every worker |
   | Reuse answers | `job.cache.get(question)` / `job.cache.put(question, value, seconds)` |
   | A file an operator loads (a published list) | `job.references.info(name)`, `job.references.local_copy(name)` (gives `(path, info)` of the same file), and a `tools()` command that calls `context.references.put(name, raw_bytes, '.csv', as_of='YYYY-MM-DD', details={...})`. Names: lower case letters, digits and `_` |
   | A CLI command | `tools()` returns `{'load': Tool(help, add_arguments, run)}`: `agents tool <agent> load ...` |
   | Say what is missing | `readiness()` returns `'ok'` or a short reason: `ok` / `needs setup` in `/health/ready`, the reason in `GET /v1/agents` |
   | Rules for list searches | `lookups.screening.screen_outcome()`: a name alone is never a `match` |

   The registry checks the definition at start (name, key prefix, input kind, data model,
   sections), and the worker checks every record's shape before it is saved.

3. **Register it**: one line in `agents_service/agents/registry.py`:

   ```python
   AVAILABLE = {
       'cv': 'agents_service.agents.cv.agent',
       'linkedin': 'agents_service.agents.linkedin.agent',
       'certificate': 'agents_service.agents.certificate.agent',
   }
   ```

4. **Add tests** in `tests/agents/certificate/` (copy `tests/api/test_new_agent.py`). Tests never
   call a real outside service: give the agent's outside call a stand-in.

5. **Switch it on** where it should run: `AGENTS_ENABLED_AGENTS=cv,linkedin,certificate`.

6. **Give keys** to the projects that need it: `agents create-key --project hr --agent certificate`.
   Try it: `agents try --agent certificate --key <key> --file x.pdf`, or for a lookup agent
   `agents try --agent npi_lookup --key <key> --data '{"number": "1234567893"}'` (on Windows,
   put the JSON in a file and use `--data-file`). An agent with its own settings may not be
   named like the service's settings (`model`, `s3`, `key`, ...): its `AGENTS_<NAME>_...`
   would reach them, so the registry refuses it.

7. Run `agents check`, add a line to `docs/changelog.md`, and optionally add cases to
   `accuracy/cases.json`.

## Rules

- Never import another agent; put shared code in `documents/`, `ai/` or `agents/records.py`.
- Never call a model directly; use `agents_service.ai.providers.provider`.
- Return facts only, with their quotes. Project rules (name checks, scoring) belong in the projects.
- Raise `ExtractionError('safe message')` for a file problem the caller may see; never put file
  content in messages or logs.
