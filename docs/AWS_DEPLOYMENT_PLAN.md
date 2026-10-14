# AWS deployment plan — CPU pilot

Status: proposed, not deployed or approved for spending. Price research date: 2026-10-14. Currency USD, before tax/support. Region assumption: **us-east-1 (N. Virginia)** for an initial cost worksheet only. Confirm residency, client geography and the actual AWS account before deployment. No existing AWS resources were inspected.

## Pilot shape

Start with one CPU EC2 host: HTTPS reverse proxy, API, database-backed worker queue, PostgreSQL, OCR subprocesses and private Ollama, isolated by process/container permissions. Encrypted EBS persists model and database on separate volumes/directories; encrypted local persistent storage holds uploads/results in the implemented pilot; S3 integration is deferred. Do not put the original Kormic application on this host. Single-host failure interrupts service: this is a non-HA pilot, not production architecture.

Candidates: t3.large (2 vCPU, 8 GiB) for sporadic synthetic trials; t3.xlarge (4 vCPU, 16 GiB) for more memory and OCR headroom. Compare a non-burstable compute/general-purpose candidate, such as c7i.xlarge or m7i.large, if sustained CPU demand exhausts credits; price it in the approved region before selection. No instance is declared suitable without measurements. No GPU candidate is selected; GPU requires measured CPU insufficiency and separate cost approval.

Q4 weights have a rough lower-bound calculation of 1.7 billion ×4 bits ≈0.85 GB before quantization metadata and unquantized tensors. This is **not actual artifact size or resident memory**. Provision an initial total-host planning envelope of 4–8 GiB for model, KV cache, buffers, API/DB and sequential OCR, with substantial uncertainty. Benchmark 8 GiB and consider 16 GiB if peak RSS exceeds 80%. Larger context and parallel inference increase memory. One inference slot and one rendering/OCR subprocess initially; bound competing work.

Workload assumption: 100 jobs/day maximum, 50 CV/30 LinkedIn/20 GitHub; typical CV<=5 pages, LinkedIn<=5 screenshots, GitHub<=3 repos. This is a planning scenario, not demonstrated capacity. A 30-page scanned CV or ten-image job is a separate stress case. Upload budget illustration: 100 jobs ×5 MiB average ≈500 MiB/day; seven days would be ~3.5 GiB before results and overhead. Budget 10 GB upload occupancy. Real uploaded data distribution can be much larger.

## Network and access

Pilot public exposure: HTTPS 443 to the reverse proxy only, ideally restricted to approved client egress IPs in addition to API auth. Never publish Ollama 11434, PostgreSQL 5432 or worker/admin endpoints. No public SSH; use approved SSM access. Host firewall and container network rules must independently enforce this, including IPv6. DNS and certificates are deployment prerequisites; no illustrative URL is live.

For a single host, Ollama binds localhost if processes share host networking. With containers, use an isolated network without published Ollama port and a worker-only inference proxy. Do not accidentally make container localhost point at the wrong service. Management endpoints are separately restricted. Runtime cloud features disabled, model process egress denied. Only the collector can access GitHub API/OAuth over HTTPS; AWS storage/monitoring calls use least-privilege instance roles. GitHub collection, OAuth exchange and refresh require internet; inference can run locally after dependencies are provisioned, but the overall service is not fully offline.

Cost-conscious pilot can use a public-subnet host with one public IPv4, inbound restrictions and outbound controls, avoiding a NAT gateway. This does not make Ollama public. A stricter private-subnet deployment with load balancer/NAT/endpoints is an alternate architecture requiring a new cost quote. Do not silently add those services. DNS-validated certificates can avoid an open HTTP listener; choose and test certificate renewal before launch.

## Storage, IAM and operations

Proposed total gp3 EBS 60 GB: 20 GB OS/application, 20 GB model storage, 20 GB database/working capacity. Adjust after measured artifact/import duplication and disk headroom. Preserve model volumes across host replacement and verify digest on boot; no automatic model downloads. Never store customer uploads on a model snapshot. Encrypt volumes and S3; block public access, enforce TLS and restrict bucket prefixes by application ownership checks. No public read ACLs or default exposed media URLs.

Use an instance role for exact bucket and secret permissions; no embedded AWS access keys. Separate deployment role from runtime. Restrict KMS decrypt to relevant keys/resources. Store OAuth token encryption material and service-key hash secret separately from the DB. A shared runtime role is a pilot compromise; application tenant isolation remains essential because IAM alone will not separate tenant rows.

Retention follows [API_CONTRACT.md](API_CONTRACT.md): 24-hour orphan uploads; raw attached inputs expire 24 hours after terminal processing or earlier job expiry; seven-day results/OCR/evidence; fenced deletion; no content backups in pilot. Daily encrypted minimal-metadata backup is optional only if tokens/content are excluded, with seven-day expiry and deletion-ledger replay. AWS S3 lifecycle is a cleanup backstop, not proof of immediate deletion. If versioning is later enabled, delete noncurrent versions too and revise deletion claims.

Monitor queue age/depth, oldest tenant wait, error/partial rate, inference readiness, CPU credits, RSS, disk, OCR timeouts, latency, bytes stored and deletion backlog. Logs are metadata-only with seven-day retention initially. Budget notifications at 50%, 80% and 100% of approved spend plus daily anomaly alerts; alerts are not a hard spending cap. Operator owns incident response and shutdown decisions.

## Cost worksheet

Official AWS T3 page currently lists Linux/Unix N. Virginia t3.large at $0.0835/hour and t3.xlarge at $0.1670/hour. Another official AWS guidance page displays $0.0832/$0.1664; use the higher live instance-page values for this provisional worksheet and resolve the discrepancy with an account/region AWS Pricing Calculator quote before approval.

| Component | Basis | Monthly planning amount |
|---|---|---:|
| t3.large at 176 hours | 8 hours ×22 days ×$0.0835 | $14.70 |
| t3.xlarge at 176 hours | 176 ×$0.1670 | $29.39 |
| t3.large at 730 hours | 730 ×$0.0835 | $60.96 |
| t3.xlarge at 730 hours | 730 ×$0.1670 | $121.91 |
| gp3, 60 GB throughout month | Provisional $0.08/GB-month; AWS page gives this as regional example, reconfirm region | $4.80 |
| One retained public IPv4 | 730 ×$0.005 including idle time | $3.65 |
| S3, requests, small permitted metadata backups | 10 GB occupancy budget; allowance, not validated regional quote | $1–5 |
| DNS/certificate operations, secrets/KMS, metadata monitoring | Allowance pending selected services and usage | $5–15 |
| Internet transfer | Reserve for <=10 GB outbound/month; not assuming free-tier credits | $0–2 allowance |
| Contingency | Unmeasured requests/storage/operational overhead | $5–10 |

Provisional totals excluding surplus CPU credits: **$35–70/month for scheduled trials** and **$80–165/month for continuous operation**, spanning the two T3 candidates and allowances. EBS and retained IPv4 continue to cost money when EC2 stops. Actual bills depend on region, traffic, taxes and selected services. AWS is not free because Qwen is local.

T3 Unlimited surplus CPU credits are a material extra, not hidden in the estimate. Official Linux T3 surplus pricing is $0.05/vCPU-hour. At an extreme sustained 100% CPU with no remaining earned credits, rough additional rates are 2×(1−0.30)×$0.05=$0.07/hour for large and 4×(1−0.40)×$0.05=$0.12/hour for xlarge. That is approximately $12.32/$21.12 over 176 hours or $51.10/$87.60 over 730 hours, subject to credit accounting. Corresponding conservative envelopes become roughly **$35–95 scheduled** or **$80–255 continuous**. Standard credit mode avoids surplus charges but can throttle severely. Choose mode explicitly; benchmark after credits are depleted. A non-burstable instance may be better value for sustained inference.

No RDS, ALB, NAT gateway, GPU, commercial certificate, support plan, WAF or multi-AZ redundancy is included. These are not silently assumed free. If required, revise architecture and quote before deployment. No savings plan/reservation should be purchased for an unbenchmarked pilot.

Pricing sources checked 2026-10-14:

- [EC2 T3 specifications and prices](https://aws.amazon.com/ec2/instance-types/t3/)
- [EC2 On-Demand pricing and CPU credits](https://aws.amazon.com/ec2/pricing/on-demand/)
- [Alternative AWS guidance with differing T3 figures](https://docs.aws.amazon.com/prescriptive-guidance/latest/optimize-costs-microsoft-workloads/right-size-selection.html)
- [EBS pricing](https://aws.amazon.com/ebs/pricing/)
- [VPC public IPv4 pricing](https://aws.amazon.com/vpc/pricing/)

Other rows are explicitly allowances; obtain precise S3, CloudWatch, Secrets Manager/KMS, DNS and transfer rates from the approved account's calculator before spend approval. This is a planning range, not a vendor quote.

## Scale, stop and deploy gates

Scheduled pilot: stop admission before shutdown, drain/fence workers, flush DB, stop instance, retain encrypted volumes. Communicate downtime; clients receive service-unavailable rather than endless queues. Warm-up and readiness must complete before accepting new jobs on restart. Keep model volume mounted and verify retention cleanup after downtime.

Scale based on measured queue-age and per-job service time, not just CPU. First use bounded queues and fair tenant scheduling. If demand justifies more hosts, separate API, managed DB/queue and model workers; each worker retains the same approved artifact. Never increase model parallelism without memory testing. Production HA, backups and recovery targets need a separate review and cost plan.

Before pilot deployment: approve region/budget/account, complete artifact import and task benchmarks, resolve source-license permission, configure domain/TLS, verify external scans cannot reach Ollama, run negative tenant/deletion tests, and test restart/model persistence. Deploy only with explicit infrastructure approval. Observe actual latency and spend for at least seven representative operating days; no production-readiness claim based on this plan alone.

## Implementation delta and release gates

The included Compose template is single-host only. It uses PostgreSQL for queue/state and local volumes for uploads; no S3, AWS provisioning, IAM configuration or cloud monitoring is implemented. S3 prices above are a future allowance, not a claim of current use. Model network is internal-only and inference proxy denies management paths; only HTTPS publishes a host port. Image digest variables intentionally require operator selection. Container builds, certificate renewal, PostgreSQL transaction concurrency, network denial and host resource limits have not been tested here.

Before a pilot: approve region/residency/cost ceiling; pin and scan all images including the Python base; set tested memory/CPU/pid/disk bounds; verify 11434/5432/8000 inaccessible externally and model egress denied; inspect Docker IPv6/firewall behavior; disable query/body/access logging at every proxy; install monitoring and cleanup alerts; verify encryption and no content backups; exercise restoration and token revocation. The parser is a resource-limited subprocess with scrubbed environment, but shares filesystem/network namespace with the worker. Stronger parser sandboxing and dedicated collection egress are required before hostile multi-tenant uploads. Single-host file locking must never be treated as a distributed lease. No deployment has occurred.
