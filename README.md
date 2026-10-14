# Univarsal Agents

Independent pilot service for CV, LinkedIn screenshot and GitHub repository extraction. Clients call an authenticated HTTPS API; parsing, OCR, agents and Qwen inference run in the operator's AWS environment. Clients need no Ollama installation. No hosted LLM SDK or paid-provider fallback is included.

**Status:** implementation and automated tests; not deployed or production-qualified. The selected `Qwen3-1.7B-Q4_K_M.gguf` artifact is not available in this workspace. No model has been downloaded, imported or benchmarked. Real inference, live OAuth, PostgreSQL and container deployment still require validation. No real API URL exists.

## Documentation

- [API contract](docs/API_CONTRACT.md)
- [Migration manifest](docs/MIGRATION_MANIFEST.md)
- [Implementation plan and status](docs/IMPLEMENTATION_PLAN.md)
- [Open questions and risks](docs/OPEN_QUESTIONS_AND_RISKS.md)
- [Model evaluation plan](docs/MODEL_EVALUATION_PLAN.md)
- [AWS deployment plan](docs/AWS_DEPLOYMENT_PLAN.md)
- [Ollama setup](docs/OLLAMA_SETUP.md)
- [Validation record](docs/VALIDATION.md)

## Development setup

Linux and Python 3.12 are required for parser resource limits and worker file locking. Install system Tesseract (English trained data) and Poppler. Use synthetic documents only during development.

```sh
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r requirements.lock
pip install -e '.[test]'
agents generate-secrets
```

Privately export the generated AGENTS_KEY_PEPPER and AGENTS_TOKEN_ENCRYPTION_KEY. Settings read environment variables, not `.env` automatically; Compose reads `.env` explicitly. Never commit real secrets or paste their output into logs. Set AGENTS_DATA_DIR to a private writable directory and AGENTS_DATABASE_URL to its SQLite path for development (default data/service.db). PostgreSQL is the proposed AWS backend.

```sh
agents init-db
agents create-key --tenant synthetic-demo
uvicorn extraction_service.api:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
# Separate terminal with the same environment:
agents worker
```

Use the one-time printed key from a client backend. See the contract for upload → submit → poll examples. Without verified model configuration, jobs report model availability errors; they do not download models or use a hosted fallback. Database initialization is for a fresh schema, not a schema-migration system. Keep application and schema versions together.

Run `pytest -q` and `ruff check extraction_service tests`. Tests use synthetic fixtures and mocked inference/GitHub responses except real local parser/OCR checks. `agents model-check` performs an actual synthetic inference only after approved model provisioning. It is not a quality benchmark. `agents sweep` manually runs cleanup. `agents revoke-key --id <key-id>` revokes a service key.

## Pilot deployment template

`compose.yaml`, `Dockerfile` and `deploy/` are preparation artifacts, not a deployed installation. Docker was unavailable during this implementation; build/start/network tests are still required. Image digests must be selected and approved; required image variables deliberately have no defaults. Copy `.env.example` privately, configure secrets, database password/URL and approved hostname. Pin the Dockerfile base digest before deployment too.

After infrastructure/model approval: build the image, start the DB, run `docker compose --profile tools run --rm admin agents init-db`, provision the model using the documented procedure, create tenant keys, then start API/worker/HTTPS services. Verify every readiness/security gate in the deployment plan. Only 443 is published. DNS must point to the approved host and TLS-ALPN certificate validation must work on 443. Ollama has no published port or outbound network; a private allowlisted proxy exposes inference/readiness routes to the application. Management commands are operator-only inside the model container.

One host and one worker lock domain only. Local persistent volumes currently store uploads/results and PostgreSQL data; S3 is not implemented. Do not scale workers to multiple hosts until a distributed lease/queue and object storage design is implemented and tested. No AWS resources or spending are authorized by this template.

## Rights and scope

Implementation was written independently from reviewed requirements; original repositories, history, credentials and personal uploads were not copied or modified. Source reference: OneSmarterInc/Kormic_backend_sahil_ammar at fef5c27d547bab7f4447180be3f5da92dc031556. No original code license is asserted. Repository owner must select a license for this new service and resolve any future source reuse separately. Dependency, OCR binary and model licenses remain distinct.
