# Implementation status and next gates

2026-10-14. Coding approved for OneSmarterInc/Univarsal_Agents only. No original repository changes, model downloads or AWS deployment.

| Phase | Status | Evidence / next work |
|---|---|---|
| Source verification/planning | Completed earlier; baseline retained | Source SHA fef5c27d547bab7f4447180be3f5da92dc031556; detailed private audit inventory retained separately |
| Independent service | Implemented | Python package, centralized settings, CLI, dependency lock |
| CV | Implemented pilot | Local PDF/DOCX text, scanned/mixed-page OCR, evidence validation |
| LinkedIn | Implemented pilot | 1–10 images, local OCR, conservative merge/date validation |
| GitHub | Implemented pilot | OAuth state/encrypted token, authorized public selection, fixed-commit samples, local inference adapter |
| Shared API/jobs | Implemented pilot | Tenant/scoped keys, quotas, idempotency, FIFO DB queue, fencing, cleanup |
| Deterministic testing | Executed | See VALIDATION.md; mocked Qwen and GitHub, actual parser/OCR tests |
| Model evaluation | Blocked on approved artifact | No GGUF locally, no real inference metrics |
| Container/PostgreSQL integration | Prepared, untested | Build/scan/pin images, initialize schema, test concurrency/network/TLS |
| AWS pilot | Not deployed | Separate region/spend/infrastructure approval required |
| Production review | Not ready | Evaluation, sandboxing, operations, rights and deployment gates remain |

## Ownership of new files

`api.py` owns authentication boundary and routes; `service.py` tenant/job/upload lifecycle; `db.py` independent persistence; `schemas.py` strict request/model observations and field whitelist; `parsers.py` bounded local parsing/OCR subprocess; `extractors.py` source-bound evidence/merge; `inference.py` centralized private Ollama transport; `github.py` OAuth/API collection; `worker.py` orchestration/fencing; `config.py` settings; `cli.py` operator commands. None imports the source repository's ORM or providers. Tests are independently authored synthetic fixtures.

No candidate acceptance/rejection, mismatch scoring, admissions, student-profile writes, notifications or original application database dependencies. Scope is extraction and evidence. Record schema is deliberately simpler than the original application; clients must map it explicitly.

## Next sequence

1. Review this pilot API and documented differences: public-only GitHub, English OCR, one-host FIFO queue, generic CV records, no S3 or webhooks.
2. Approve exact Qwen artifact provenance and acquisition. Select compatible Ollama, verify metadata/license/hash/import/template, run synthetic inference and held-out evaluation. No model substitution without approval.
3. Harden sandbox/egress and observability, pin/scan/build images, test PostgreSQL, OAuth end-to-end and HTTPS/negative network paths. Add schema migration tooling before changing a live DB.
4. Approve AWS region, residency, instance candidate, operating schedule, monthly limit and infrastructure. Deploy synthetic-data pilot only after those gates.
5. Measure latency, memory, throughput, accuracy and actual costs. Production requires operational ownership, recovery and security review as well as model quality.

The active deadline is checked between bounded operations, not a hard process-wide wall clock. SQL gate serializes writes and shared flock serializes workers; this is intentionally limited to one host. Whole-job retry on crash can repeat read-only GitHub/inference calls. No distributed correctness claim is made.
