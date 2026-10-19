# Model evaluation plan

Status: proposed protocol; **no benchmark results exist yet**. Model under evaluation: the selected Qwen3-1.7B-Q4_K_M.gguf, not yet obtained, once provenance and import are verified. One artifact across CV, LinkedIn and GitHub. No fine-tuning, larger model, alternate weights or hosted inference is authorized.

## Dataset and ground truth

Use synthetic or explicitly authorized material only. Keep source documents, ground truth and case lineage outside source control. Split by person/template/repository family, preventing near-duplicate leakage between development and held-out sets. Proposed minimum: 30 development and 100 held-out cases per task; add 30 adversarial/invalid cases per task. A LinkedIn case is a set of 1–10 screenshots, a GitHub case 1–5 repositories. Include 30 maximum-size stress jobs across tasks in addition to typical examples.

CV held-out strata: 25 ordinary text PDFs, 20 DOCX with tables, 20 scanned PDFs, 15 mixed native/image PDFs, 10 complex two-column layouts, 10 sparse/ambiguous CVs. Include 1-, 5- and 30-page specimens, missing names/contact fields, publications, dates and GPA scales. Boundary/negative suite includes 31 pages, 36,001 extracted characters, oversized files, wrong MIME, password protection, ZIP bombs, external relationships and malformed streams.

LinkedIn held-out strata: 20 full readable sets, 20 cropped/missing sections, 20 multi-image overlaps and duplicates, 15 low-resolution/rotation/compression cases, 15 dates/course-versus-job ambiguities, 10 contradictory scalar observations. Include absence of contact fields and prohibit email/phone invention. Test one and ten screenshots, mixed readable/unreadable inputs, the eleventh-image rejection and 25-million-pixel boundary. Add prompt injection disguised as profile text.

GitHub held-out strata: 20 single-language projects, 20 mixed-language projects, 20 minimal/empty/documentation-only repositories, 20 README-versus-source contradictions/forks, 20 large or truncated trees with sparse samples. Use authorized repositories or synthetic inert source bundles; never execute their contents. Maintain recorded API fixtures for deterministic offline collector tests, plus separately authorized live OAuth/API cases for grant, identity, scope and rate-limit behavior. Include secret-like strings and injection instructions. Do not assert authorship or proficiency as ground truth.

Two annotators label independently, then adjudicate disagreement. Define truth relative to visible uploaded material or examined repository evidence, not external claims. Annotate exact field values, absent/unreadable/unsupported states, source locations, acceptable normalization and each unsupported claim. GitHub summaries get a bounded rubric for factual support, not exact-string matching. Record OCR reference text for image regions. Annotators must not use the model answer as the gold label.

## Metrics and pre-run acceptance gates

These are proposed pilot acceptance criteria requiring review before the held-out run. Evaluate each task independently and publish denominators, strata and 95% confidence intervals; never hide poor OCR performance in a pooled score.

| Metric | Definition | Pilot gate |
|---|---|---|
| Scalar precision | Correct returned supported values / all returned values | >=98% for CV/LinkedIn identity/contact fields; >=95% other supported scalars |
| Readable-field recall | Correctly recovered gold values / gold values visible in readable examined material | >=90% per task; >=85% in each difficult OCR stratum |
| Collection extraction | Record matching then field precision/recall, micro and macro F1 | Macro F1 >=0.90 for CV and LinkedIn records |
| GitHub support | Correct evidence-supported analysis claims / analysis claims | >=95%, with zero authorship/proficiency/test-execution claims |
| Schema validity | Valid full response before and after bounded repair | >=98% first pass; >=99.5% final; all invalid responses withheld |
| Unsupported claims | Unsupported emitted claims / emitted factual claims, before and after validator | <=1% post-validation; zero invented identity/contact values in held-out set |
| Missing-field errors | Visible gold fields incorrectly absent or wrong status / visible gold fields | <=10%; separately report omission caused by OCR, sampling and model |
| Evidence | Valid source-local references and source-supported value | 100% references resolve; >=98% semantic support; no cross-source contamination |
| OCR | Character/word error rate against image reference text | Report by stratum; <=5% median character error on clean English screenshots/scans |
| Routine failures | Failed jobs / valid typical-input jobs after retry budget | <=2% per task; report partial separately |
| Retry rate | Jobs requiring model/schema retry / valid typical jobs | <=10% per task |
| Warm processing p95 | No queue wait; CV<=5 pages / LinkedIn<=5 images / GitHub<=3 repos | <=120s / <=180s / <=180s respectively |
| Maximum-size behavior | All published size limits, measured separately | Target: completes or reports partial/failure within 30 minutes; current deadline checks occur between bounded operations and can overrun by one operation |
| Memory | Whole-host working set and component peak RSS | <80% host RAM at steady maximum admitted work; zero OOM; no sustained swap |
| Concurrency | 1, 2 and 4 submitted jobs; 1 model inference slot initially | No cross-job data, duplicate publication or unbounded backlog; measure FIFO starvation before approving capacity |
| Security | Tenant escape, secret logging, model network escape, deleted-data resurrection | Zero observed successes in mandatory negative suite |

Zero observed identity hallucinations is a test gate, not proof of zero population risk. A 100-case set cannot establish very rare failure rates precisely; report the uncertainty and expand tests before production. Schema validity alone is not extraction accuracy.

## Execution protocol

Record artifact SHA, source provenance, Ollama version/digest, Qwen template, schema/prompt versions, OCR engine/language data versions, CPU architecture/vCPUs/RAM, storage and OS/container versions. Record configured context/output budgets and actual token counts. Run no unapproved model downloads.

1. Verify import, model identity, local-only traffic and minimal valid structured response.
2. Run deterministic parser/schema/evidence/OAuth-fixture tests without inference to isolate integration failures.
3. Tune prompts/chunking/normalization only on development cases. Freeze configuration and acceptance gates.
4. Run held-out set once for final scoring; repeat a representative 20-case/task subset three times to measure variance. Any tuning afterward requires a fresh held-out run/version.
5. Separate cold startup/model load, OCR, prompt prefill, generation, validation, queue wait and GitHub network latency. Keep a two-hour sustained load test after T3 credits deplete or explicitly constrain CPU credits; a short burst benchmark is not steady-state performance.
6. Evaluate traffic mixes (50% CV, 30% LinkedIn, 20% GitHub proposed), serial inference with 1/2/4 submitting clients, queue fairness, forced worker restart, model outage, malformed JSON, token truncation, disk-full and deletion during inference. Report throughput jobs/hour and tokens/second; no assumed capacity.
7. Compare OCR-only versus oracle text input to identify OCR errors. Compare metadata-only GitHub output with analysis to identify added unsupported claims. Inspect rejected results as well as accepted ones, avoiding survivor bias.

Store results as per-case field/claim metrics, aggregate summary and sanitized failure catalog. Raw personal content remains under the approved evaluation retention policy. Never log source material to hosted tracing services.

## Failure response

If a task fails gates, document examples and classify OCR, parsing, coverage, schema, prompt, evidence validation or model limitation. First improve deterministic extraction, section-sized prompts and validation; measure any extra latency. Do not silently lower criteria, upgrade Qwen or switch providers. A restricted pilot may enable only tasks/inputs that passed if the user approves and capabilities reflects the restriction. Failure on all reasonable configurations means the selected artifact is unsuitable for the required scope; seek a separate model decision.

## Current results register

| Item | Actual status |
|---|---|
| Actual GGUF identity/license/hash | Unverified; artifact unavailable in task workspace |
| Ollama version/import compatibility | Not selected or tested |
| CV / LinkedIn / GitHub benchmark | Not run |
| Memory / latency / throughput | Not measured |
| Production suitability | Undetermined |
