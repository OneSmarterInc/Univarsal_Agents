# Running on a server (Docker) and on AWS

Phase 2. The service runs as Docker containers on one server; growth adds servers
on the same job line (docs/architecture.md).

## What runs

| Container | Command | Does |
|---|---|---|
| `postgres` | PostgreSQL 16 | Keys, jobs, results, usage, the OIG list |
| `ollama` | Ollama | The model (`kormic-local`, Qwen3) |
| `model-init` | runs once | Downloads `qwen3:<MODEL_TAG>` and names it `kormic-local` |
| `migrate` | `agents init-db`, runs once | Creates or upgrades the tables |
| `api` | `agents serve` | The HTTP API on port 8000 (this server only) |
| `worker-docs` | `agents worker --agents cv,linkedin` | Reads CVs and screenshots with the model |
| `worker-lookups` | `agents worker --agents nppes,oig,sam,github` | Outside lookups (seconds each) |
| `caddy` (profile `https`) | Caddy | HTTPS for `API_DOMAIN` with a free certificate |

Files: `deploy/Dockerfile`, `deploy/compose.yml`, `deploy/compose.gpu.yml`,
`deploy/Modelfile`, `deploy/env.example`, `deploy/oig-refresh.sh`, `deploy/backup.sh`.
The image runs as user `agents` (uid 10001), never root. Uploaded files share the
`files` volume between the API and the workers on one server; with several servers set
`AGENTS_STORAGE=s3`.

## Start on any Linux server with Docker

```sh
git clone -b production-ready https://github.com/OneSmarterInc/Universal_Agents.git /opt/universal-agents
cd /opt/universal-agents
cp deploy/env.example deploy/.env
# fill in AGENTS_KEY_PEPPER, POSTGRES_PASSWORD (and the SAM key, GitHub token, API_DOMAIN)
docker compose -f deploy/compose.yml --env-file deploy/.env up -d --build
# with an NVIDIA GPU:       add  -f deploy/compose.gpu.yml
# with HTTPS on this server: add  --profile https
docker compose -f deploy/compose.yml --env-file deploy/.env logs -f model-init   # first start: model download
curl -s http://127.0.0.1:8000/health/ready
```

Keys and the OIG list:

```sh
alias agents='docker compose -f deploy/compose.yml --env-file deploy/.env run --rm --no-deps api'
agents create-key --project kormic --agent cv
agents model-check
deploy/oig-refresh.sh            # loads this month's LEIE list
```

Monthly and daily jobs (cron as the server's admin user):

```
17 3 12 * *  /opt/universal-agents/deploy/oig-refresh.sh >> /var/log/oig-refresh.log 2>&1
41 2 * * *   BACKUP_BUCKET=s3://<bucket> /opt/universal-agents/deploy/backup.sh >> /var/log/agents-backup.log 2>&1
```

Update to a new version:

```sh
git pull
docker compose -f deploy/compose.yml --env-file deploy/.env up -d --build
```

`worker-docs` gets 15 minutes to finish the CV it is reading before it stops; a job it
could not finish returns to the line.

## AWS shape (for approval)

One EC2 server runs everything above. Nothing is created until the owner approves.

| Part | Choice | Why |
|---|---|---|
| Server | EC2 `g4dn.xlarge` (NVIDIA T4 16 GB, 4 vCPU, 16 GiB) | Qwen3 4B on a GPU: a CV in well under a minute instead of 1–8 minutes on the laptop CPU |
| Disk | 100 GB gp3, encrypted | Image, model (about 3 GB for 4B), database |
| Operating system | Ubuntu 24.04 with the NVIDIA driver (AWS Deep Learning Base AMI) | Driver and NVIDIA Container Toolkit come installed |
| Network | Security group: 443 (and 80 for the certificate) from the internet, nothing else | No SSH port: admin through SSM Session Manager |
| HTTPS | Caddy on the server, a DNS name such as `agents.<domain>` | Free certificate, no load balancer cost; move to an ALB when there are 2+ servers |
| Secrets | SSM Parameter Store (SecureString): key pepper, database password, SAM key, GitHub token | Written into `deploy/.env` at start, never in git |
| Instance role | Read its own SSM parameters, write backups to one S3 bucket | Nothing else |
| Metadata | IMDSv2 only, hop limit 1 | Containers cannot read the server's role |
| Backups | `deploy/backup.sh` daily to an S3 bucket (encrypted, 30-day expiry); weekly EBS snapshot | Keys and usage survive a lost server |
| Monitoring | CloudWatch agent: CPU, GPU, disk, `/health/ready`; alarm by email | |
| Outbound | `npiregistry.cms.hhs.gov`, `api.sam.gov`, `api.github.com`, `oig.hhs.gov`, the model download | Lookup agents and setup |

Approximate cost, us-east-1, on demand (Oct 2026): `g4dn.xlarge` $0.526 an hour,
about $384 a month; `g6.xlarge` (L4 24 GB, for Qwen3 8B) $0.805 an hour, about $588 a
month. A 1-year savings plan takes about a third off. Disk, backups and traffic add about
$15–25 a month. Prices differ by region; check the AWS pricing page before approval.

A CPU-only server (`t3.xlarge`, about $121 a month) also works with Qwen3 1.7B, at
laptop speed (1–8 minutes per CV). It suits a trial, not Kormic's sign-up flow.

### Growth

1. More CVs at once: a second `worker-docs` on the same server (GPU memory permitting).
2. More servers: `AGENTS_STORAGE=s3`, the database on RDS PostgreSQL, an ALB in front.
3. A busy agent on its own server: `agents worker --agents cv`.

## Before production

- [ ] SAM.gov System Account key (`AGENTS_SAM_API_KEY`, `AGENTS_SAM_DAILY_LIMIT`)
- [ ] GitHub token with no scopes (`AGENTS_GITHUB_TOKEN`)
- [ ] `AGENTS_SAM_MAPPING_VERIFIED=true` (checked on the laptop on 23 Oct 2026)
- [ ] A new `AGENTS_KEY_PEPPER` for AWS (laptop keys never work there)
- [ ] OIG list loaded and the monthly refresh in cron
- [ ] Daily backup in cron, and one restore tried
- [ ] Re-run the 7 student CVs and the LinkedIn screenshots on the server's model
