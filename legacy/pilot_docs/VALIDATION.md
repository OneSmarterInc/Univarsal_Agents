# Validation record

Executed 2026-10-14 in Linux, Python 3.12.14, against this independently written service.

| Check | Actual result |
|---|---|
| pytest -q | 47 passed, 1 warning, 6.38 seconds on final run |
| ruff check extraction_service tests | Passed |
| ruff format --check extraction_service tests | 17 files already formatted |
| git diff --check | Passed |
| pip-audit -r requirements.lock | 32 runtime packages; zero known vulnerabilities reported at audit time |

Tests include actual native PDF, DOCX table parsing and scanned-PDF Tesseract/Poppler OCR; file/page/text limits; malformed files, archive decompression and external relationships; Unicode chunk coverage; date regression; missing/conflicting/unsupported claims; multi-screenshot partial results; scoped authentication, tenant separation, quotas, idempotent concurrent submission, expiry, deletion fencing, revoked credentials and interrupted workers; bounded model retries/truncation/timeouts; fixed GitHub origins, public repository filtering, metadata preservation during inference outage and one-use OAuth state. Fixtures contain synthetic data only.

Model responses and GitHub HTTP are mocked. No real Qwen GGUF, Ollama import, live GitHub OAuth flow, model quality/latency/memory/concurrency benchmark or AWS deployment was run. SQLite was exercised; PostgreSQL and Docker/Compose were not available for integration validation. Container/OS vulnerability scans and egress/TLS tests are pending. Dependency audit is a point-in-time lookup, not proof of overall security.

One warning: Starlette's test client deprecates its current httpx backend in favor of httpx2. It did not cause a test failure; track upstream compatibility before upgrading the pinned test stack. Runtime HTTP calls continue to use the installed httpx adapter.

No existing repository tests were run and no assertion is made about their results. Original repositories were not modified. Production readiness is explicitly undetermined; see OPEN_QUESTIONS_AND_RISKS.md and MODEL_EVALUATION_PLAN.md.
