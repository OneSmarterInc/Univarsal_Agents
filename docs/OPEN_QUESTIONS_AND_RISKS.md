# Open questions and risks

## Blocking model and deployment decisions

- Exact approved Qwen3-1.7B Q4_K_M binary source, SHA256, tokenizer/template and license notices are unavailable. No actual artifact verification, import or model benchmark is possible yet.
- Ollama version/image digest, AWS account/region, residency, DNS, budget, operating schedule and infrastructure approval remain open.
- Live GitHub OAuth app configuration, callback URL, grant/revocation behavior and authorized test accounts are not validated. Pilot intentionally filters private repositories even if a historical token has a broader grant. Token refresh is not implemented; expired/revoked tokens require reconnection.
- Original source code is not copied. A license for the new repository and permissions for any future source reuse need owner decisions. Model and third-party licenses must be inventoried separately.

## Known implementation limits

- No actual Qwen quality, latency, memory, throughput or concurrency benchmarks. JSON validity and literal quote matching do not establish correct entity/record association. Injected text may still influence which supported substrings are selected. Evidence is an observation, not truth/authorship/proficiency verification.
- English Tesseract only. DOCX inline-image OCR, headers/footers and complex reading order are incomplete. Generic CV record schema does not preserve every original specialized field. Partial overlapping records may duplicate. Dates are retained raw and flagged; month-order normalization is not implemented.
- One host, one shared filesystem lock and FIFO DB queue; no distributed lease, fairness guarantees, horizontal scaling or stage checkpoints. Admission is bounded, but resource usage/queue latency requires measurement. Global DB write lock trades scalability for simple atomic pilot operations.
- Parser subprocess has memory/CPU/file/output/time limits and no secret environment, but shares host/container network and filesystem with the worker. This is not a hardened sandbox against parser exploitation. Separate network/filesystem isolation is a pre-production gate; use synthetic/authorized controlled inputs for the first pilot.
- API body limits do not replace proxy slow-client protections, connection limits or a WAF. Unexpected exceptions are suppressed from payload logging, but deployment logs/traces/proxies must also be reviewed. No source contents should reach hosted tracing systems.
- DB/storage encryption, TLS, IAM, secrets manager integration, backups, monitoring, cost alerts and operator runbooks require deployment work. Compose has no production HA, automatic restart policy, resource tuning or backup policy. Placeholder image digests and base-image pinning must be resolved before deployment.
- Cleanup is asynchronous; downtime delays physical deletion. SQLite/PostgreSQL WAL and filesystem blocks may retain deleted bytes until reuse/disposal. No immediate secure-erasure or backup-deletion guarantee. No content backups are configured.
- Docker image build, Compose networking, PostgreSQL behavior and live OAuth have not been exercised here. Dependency audit scope is Python runtime packages, not OS/container/model supply chain.
- Strict local-origin hostname configuration is not itself network isolation; DNS and container/host egress policy must enforce approved routing. Ollama must remain inaccessible publicly, including management endpoints.

## Review questions

Confirm public-only GitHub is acceptable for the pilot; approve field schema and retention; confirm English-only OCR and synthetic-data corpus; choose artifact provenance and owner; approve region and monthly ceiling; nominate deployment/security/privacy and operational owners. None is a reason to silently enable a different model or hosted inference.
