# Adding an agent

An agent is one folder. The API addresses, keys, limits, job line, usage and
`GET /v1/agents` come from its definition; nothing else changes.

## Steps

1. **Copy the template**

   ```
   agents_service/agents/_template/  ->  agents_service/agents/certificate/
   ```

2. **Edit `agent.py`**

   | Field | Example |
   |---|---|
   | `name` | `certificate` (also the folder and the address `/v1/certificate/jobs`) |
   | `title`, `description` | Shown in `GET /v1/agents` |
   | `key_prefix` | `cert` (keys look like `ua_cert_...`) |
   | `input_kind` | `files`, or `data` with a pydantic `data_model` for JSON input |
   | `file_rule()` | Accepted endings, media types, number of files, size |
   | `sections` | Every section a record can have |
   | `reader_version` | `certificate/1`; change it whenever the reading changes |
   | `uses_model` | `True` if `run()` asks a model |
   | `run(job)` | Reads `job.files` (or `job.data`) and returns `AgentResult(records, files, warnings)` |

   Use the shared tools: `read_text()` for files, `provider(job.model)` for model questions,
   `add_records()` / `read_files()` for records, and keep an exact quote as `evidence`
   for every fact.

3. **Register it**: one line in `agents_service/agents/registry.py`:

   ```python
   AVAILABLE = {
       'cv': 'agents_service.agents.cv.agent',
       'linkedin': 'agents_service.agents.linkedin.agent',
       'certificate': 'agents_service.agents.certificate.agent',
   }
   ```

4. **Add tests** in `tests/agents/certificate/` (copy `tests/api/test_new_agent.py`).

5. **Switch it on** where it should run: `AGENTS_ENABLED_AGENTS=cv,linkedin,certificate`.

6. **Give keys** to the projects that need it: `agents create-key --project hr --agent certificate`.

7. Run `agents check`, add a line to `docs/changelog.md`, and optionally add cases to
   `accuracy/cases.json`.

## Rules

- Never import another agent; put shared code in `documents/`, `ai/` or `agents/records.py`.
- Never call a model directly; use `agents_service.ai.providers.provider`.
- Return facts only, with their quotes. Project rules (name checks, scoring) belong in the projects.
- Raise `ExtractionError('safe message')` for a file problem the caller may see; never put file
  content in messages or logs.
