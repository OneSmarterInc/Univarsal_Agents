# API contract 1.0 — implemented pilot

Only the authenticated HTTPS API is public. No deployed URL currently exists. API keys belong to a tenant and carry explicit scopes. Send `Authorization: Bearer <key>` from the client backend. There is no browser token exchange or CORS support in this pilot. Never embed service keys in frontend code. API key creation/revocation is an operator CLI action. Foreign-tenant resources return 404.

| Method/path | Scope | Behavior |
|---|---|---|
| GET /health/live | Public | Process liveness only |
| GET /v1/capabilities | capabilities:read | Supported fields, formats and limits |
| GET /v1/readiness | capabilities:read | Verify configured model digest and identity, not quality |
| GET /v1/openapi.json | capabilities:read | Generated request schema |
| POST /v1/uploads | uploads:write | Multipart `source_type` and `file`; 201 receipt |
| DELETE /v1/uploads/{id} | uploads:write | Delete unattached upload; attached returns 409 |
| POST /v1/extractions | extractions:write | JSON plus Idempotency-Key; 202 job receipt |
| GET /v1/extractions/{id} | extractions:read | State and terminal results |
| DELETE /v1/extractions/{id} | extractions:delete | 202 fenced deletion |
| POST /v1/github/connections | github:connect | Optional subject_ref; OAuth authorization URL |
| GET /v1/github/oauth/callback | One-time OAuth state | Exchange code, return opaque connection ID |
| GET /v1/github/connections/{id}/repositories | github:connect | Public authorized inventory; page 1–100 |
| DELETE /v1/github/connections/{id} | github:connect | Remove local token and related jobs |

Separate uploads allow individual validation, bounded retries and reuse of a receipt during submission retries. This costs an extra request and requires orphan cleanup. A receipt can attach to one job only. Uploads are stored locally in the pilot; no presigned uploads or arbitrary URL ingestion.

## Limits

CV: one PDF or DOCX, 10 MiB, PDF up to 30 pages, extracted text up to 36,000 characters. TXT is not accepted. Local PDF text parsing plus per-page OCR; DOCX paragraphs/tables supported, image-only DOCX not supported.

LinkedIn: 1–10 PNG/JPG/JPEG files, 10 MiB each. Images at most 25 million pixels, side at most 20,000 pixels. All extraction is asynchronous; there is no uncapped synchronous endpoint. OCR currently English. Email/phone are unsupported LinkedIn fields.

GitHub: 1–5 distinct positive integer repository IDs from the connection's authorized inventory, public repositories only. Fixed GitHub API origins; no submitted clone URLs. Read-only metadata, languages, ten recent commits and up to ten eligible source blobs at a fixed commit. Blob size cap 100 KB; 3,500 characters per sample and 18,000 total sampled characters per repository. Binary/generated/secret-prone paths excluded. No code or tests are executed. Selection, sampling and truncation are reported.

One source of each type per job, at most three sources. JSON request 64 KiB; multipart request 11 MiB. DOCX archive guards: 2,000 entries, 50 MiB expanded, compression ratio 100, no macros/encryption/traversal/external non-hyperlink relationships. Admission defaults: 30 API calls/minute/key, 6 uploads/minute/key, 2 submissions/minute/key; 100 jobs/day/tenant, 20 pending/tenant, 100 globally. Queue is FIFO, not tenant-fair scheduling. Application quotas do not replace ingress connection/time limits.

## Request and response examples

Illustrative URLs and IDs only:

```sh
curl https://api.example.com/v1/uploads \
  -H "Authorization: Bearer $AGENTS_KEY" \
  -F source_type=cv -F file=@synthetic-resume.pdf
```

Submit the returned upload ID:

```json
{"schema_version":"1.0","subject_ref":"opaque-client-reference",
 "sources":[{"id":"resume","type":"cv","upload_ids":["upl_example"]}],
 "requested_fields":{"resume":["name","email","skills"]}}
```

Send as JSON to POST /v1/extractions with `Idempotency-Key: unique-client-request-1`. LinkedIn uses the same source shape with multiple upload IDs. GitHub source:

```json
{"id":"github","type":"github","connection_id":"conn_example","repository_ids":[123456]}
```

Job responses contain id, state, created_at, updated_at, expires_at, sources, errors and links.self. Poll that relative URL with the same tenant's authorized credentials. Only terminal jobs expose results; processing jobs do not publish checkpoints. A terminal response contains sources keyed by submitted source ID, top-level errors and model identity metadata. Each source has type, state, data, fields, evidence, inputs, coverage, warnings and errors. Example field fragment:

```json
{"data":{"name":"Alex Example","email":null},
 "fields":{"/name":{"status":"extracted","confidence":null,
   "evidence_ids":["ev_example"],"origin":"native_text","reason_code":null},
   "/email":{"status":"not_found","confidence":null,"evidence_ids":[],
   "origin":null,"reason_code":"absent_in_examined_material"}}}
```

Fields use JSON pointers, including record/array leaves. Evidence includes opaque input reference, literal quote where applicable, locator, observed_at and content SHA256. GitHub API evidence uses response-field pointers; sampled-source evidence identifies commit/path. Source provenance records where text came from, not proof of truth. Quoted support checks cannot prove correct semantic association or identity. All confidence values are null.

`extracted` = supported observation; `not_found` = absent in examined material only; `unreadable` = could not read input; `unavailable` = conflicting observations or unavailable coverage; `unsupported` = requested field not offered; `failed` = processing failure. Missing collections are []; scalar values null. Unsupported projected fields are null. Projections support top-level fields only. GitHub top-level fields are profile and repositories; each repository has bounded analysis and coverage.

CV fields: name, email, phone, location, summary, institution, major, graduation_year, gpa, gpa_scale, links, skills, languages, education, work_experience_entries, projects, publications, certifications, awards, volunteering, other_information. Record fields are conservative generic title/organization/date/description/url/technologies, not exact original ORM parity. LinkedIn: name, headline, about, location, skills, experiences, education, projects, posts, certifications. Get capabilities for the live field list.

## State, retries and deletion

States: queued → processing → succeeded/partial/failed. Any live state may become deleting → deleted. Mixed success is partial. A worker crash can restart a whole job once; this is not durable per-stage checkpointing. A shared-host file lock serializes inference and prevents two workers publishing concurrently. Fencing prevents a deleted job being restored by stale work. Do not run workers on separate hosts with this queue design.

Idempotency is scoped to tenant and API key for 24 hours: same key/body returns the same receipt; changed body gives 409. Deleted receipts remain non-readable. After expiry the key can create another job, but deleted/attached upload receipts cannot be reused. A new key after failure means a new job and new uploads. Transient GitHub/Ollama failures are reported, not silently routed to another provider. Malformed model output gets at most one corrective retry. A model timeout blocks subsequent inference until an operator confirms abandoned generation stopped and resets the guard.

Error envelope: `{ "error": { "code": "...", "message": "...", "retryable": false } }`; some errors include retry_after. Source errors are nested in terminal results. 401 invalid_credentials, 403 scope_denied, 404 resource_not_found, 409 idempotency_conflict/upload_in_use, 413 request_too_large/file_too_large, 422 invalid_request/invalid_document/invalid_repository_selection, 429 rate_limited/quota limits, 503 model_not_ready/inference_unavailable/model_timeout. Follow explicit retryable and Retry-After; use bounded client backoff. Refresh revoked OAuth connections instead of retrying blindly.

Orphan uploads expire after 24h; attached uploads after completion +24h or earlier job expiry. Results, embedded OCR quotes and evidence expire after seven days. Poll/delete immediately hides expired/deleting content; worker sweep removes physical rows/files. Deleted receipt ledger retained seven more days. Sweep runs even when idle; outage means physical cleanup can lag until recovery. No content backups are configured. Database/WAL/filesystem free blocks are not cryptographically erased by deletion; volume encryption and controlled disposal are operational requirements. No SLA for backup deletion is claimed. OAuth connection persists until disconnected; local disconnect does not revoke GitHub's remote grant. All related jobs are fenced. Revoke the grant at GitHub as well when required.
