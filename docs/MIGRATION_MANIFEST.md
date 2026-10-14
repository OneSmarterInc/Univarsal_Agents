# Migration manifest — independent implementation

Baseline: OneSmarterInc/Kormic_backend_sahil_ammar, main at fef5c27d547bab7f4447180be3f5da92dc031556. The pinned source was inspected read-only during planning. The complete per-file dependency/caller/side-effect inventory is in the separately delivered private planning manifest; it is not republished into this public repository. This document records the active boundaries and their implementation decisions. No extraction source files, tests, original history, environment files or uploads were moved/copied. Coding authorization applies only to this new repository.

## Verified baseline findings

CV: django_api/views.py ResumeUploadAPIView → django_api/services.py parse_resume → agents/resume_parser.py ResumeParserAgent.parse → agents/resume_graph.py extract_resume → pure_multi_agent/model_router.py invoke. Public PDF/DOCX, 10 MiB, 30 PDF pages, 36,000 characters; TXT internal only. Claude fallback exists for low PDF text. The fallback's page-marker behavior may miss scans; new code examines pages locally.

LinkedIn: django_api/views.py LinkedInAnalyzeAPIView → django_api/services.py analyze_linkedin → agents/linkedin_agent.py LinkedInAgent.extract → agents/linkedin_extraction/pipeline.py extract. PNG/JPG/JPEG, 10 MiB each, queued maximum ten screenshots. No explicit synchronous count cap found; that is not evidence of safe unlimited capacity. Active schema lacks email/phone. validation._dates_sane calls timezone.now without an imported timezone binding.

GitHub: active github_profiles workflow, not legacy agents/github_agent.py. django_api/views.py GitHubAnalyzeAPIView → github_profiles/sync.py selection/queue → github_profiles/runner.py execute_slice → repository analysis → github_profiles/inference.py Inference.chat. OAuth scope is empty and does not newly grant private-repository access. Current inference is Claude and persistence is application-specific. It samples metadata/content/activity, does not execute code/tests and does not establish authorship.

## Boundary decisions

License rule for every row: baseline root license grant was not established; do not copy its code without owner/rightsholder authorization and notice review. LinkedIn's SOURCE.md identifies separate upstream attribution; GitHub comments identify upstream adaptations requiring investigation. Independently written replacements below do not assert those licenses transfer. Third-party Python/OS/model dependencies need their own notice inventory.

| Exact baseline path | Responsibility / important entry point | Callers / dependencies / side effects | Decision and new boundary | Required verification |
|---|---|---|---|---|
| agents/resume_graph.py | extract_resume orchestration/schema | ResumeParserAgent; PDF parser/provider network | REPLACE: parsers, extractors, inference | Native/scanned/mixed PDF, missing/ambiguous fields |
| agents/resume_parser.py | ResumeParserAgent.parse; document provider helpers | django_api services; provider and graph | REPLACE: independent local parser; no Claude | DOCX tables, malformed/encrypted/large documents |
| agents/linkedin_agent.py | LinkedInAgent.extract facade | django_api services; active pipeline | REPLACE: worker source orchestration | Multiple/partial screenshot jobs |
| agents/linkedin_extraction/pipeline.py | extract, OCR/merge pipeline | Facade; OCR, schemas, provider | REPLACE: local OCR + shared evidence adapter | Overlap, unreadability, missing sections |
| agents/linkedin_extraction/validation.py | _dates_sane validation | Active LinkedIn pipeline | REPLACE: standard-library UTC date check | Missing dates, future/reversed dates; runtime regression |
| agents/linkedin_extraction/SOURCE.md | Upstream attribution | License/documentation boundary | INVESTIGATE before any source reuse | Verify upstream grant and required notices |
| agents/github_agent.py | Legacy agent/provider helper | Active inference imports a provider helper | EXCLUDE legacy workflow; REPLACE provider dependency | No hosted SDK/import path remains |
| github_profiles/inference.py | Inference.chat | Repository agents; Claude/network | REPLACE: centralized private Ollama | Availability, malformed/truncated JSON, timeout |
| github_profiles/github.py | GitHub API collection | Runner/analysis; OAuth token/network | REPLACE: fixed-origin bounded collector | Pagination, private filter, identity, rate-limit |
| github_profiles/sync.py | validate_selection/queue_sync | API; ORM/queue/profile side effects | REPLACE: tenant job service | 1–5 selection, authorization, atomic admission |
| github_profiles/runner.py | execute_slice background orchestration | Queue; ORM/network/checkpoints | REPLACE: independent single-host worker | Recovery, idempotency, deletion fencing |
| accounts/github_oauth.py | OAuth account grant handling | API/account models, GitHub token exchange | REPLACE: encrypted independent connections | One-use state, callback binding, revoke/reconnect |
| accounts/crypto.py | Application crypto helpers | Accounts/settings/DB | REPLACE: centralized Fernet configuration | Missing/invalid key; key rotation design pending |
| django_api/views.py | Upload/analysis/API endpoints | App auth/services/jobs/DB | REPLACE: independent FastAPI contract | Authentication, scope, limits, safe errors |
| django_api/services.py | Application extraction consumers | Views, profiles, database/provider | EXCLUDE application writes; REPLACE extraction dispatch | No student/candidate/admissions side effects |
| pure_multi_agent/model_router.py | Provider routing | Resume graph; hosted network | REPLACE: no paid fallback | Static dependency check and mocked local paths |
| pure_multi_agent/document_jobs.py | Application document jobs | Services, queue, profile persistence | REPLACE: independent queue/state | Crash recovery, delete during work, quotas |
| Dockerfile | Original application image | App-specific dependencies | REPLACE: minimal parser/API image | Build, OS scan, non-root, isolation; not yet run |

Original ORM migrations, student profiles, candidate acceptance/rejection, admissions/advising, notification and mismatch verification are EXCLUDE. No REUSE/MODIFY code copying was performed; requirements and regression scenarios were reimplemented. Anything outside this explicit boundary remains excluded until reviewed.

## Migration differences requiring review

Shared async interface caps LinkedIn at ten for every job. Scanned CVs use local OCR instead of Claude. Original output compatibility is not claimed: CV uses a smaller generic record schema, documented in API_CONTRACT.md. GitHub is explicitly public-only; organizations/account events and broad profile workflows are omitted, while recent repository commits are collected. Raw source sampling is deliberately bounded and disclosed. Queue uses new tables and a shared-host lock, not original application background jobs. No hosted inference, source execution, application profile writes or silent model fallback.
