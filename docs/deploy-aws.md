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
`deploy/Modelfile`, `deploy/env.example`, `deploy/env-from-ssm.sh`, `deploy/oig-refresh.sh`,
`deploy/backup.sh`.
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

## AWS shape (approved 24 Oct 2026)

One small EC2 server in One Smarter's AWS account, region **us-east-1**, runs everything
above. Kormic Careers runs on its own server.

| Part | Choice | Why |
|---|---|---|
| Server | EC2 `t4g.large` (2 vCPU ARM Graviton, 8 GiB), about $49 a month | The smallest server that keeps every CV inside the account |
| Model | Qwen3 1.7B on the CPU (`MODEL_TAG=1.7b`) | Same as the laptop: 2–10 minutes per CV or LinkedIn job; the lookups take seconds |
| CPU credits | `unlimited` (the t4g default) | Reading a CV keeps the CPU busy; extra credits cost a little, the server never slows to a crawl |
| Disk | 40 GB gp3, encrypted, about $3 a month | Images, model (about 1.4 GB), database |
| Operating system | Ubuntu 24.04 LTS, arm64 | |
| Network | Elastic IP; security group: 80 and 443 from the internet, nothing else | No SSH port: admin through SSM Session Manager |
| HTTPS | Caddy on the server (`--profile https`), DNS name such as `agents.<domain>` | Free certificate, no load balancer cost |
| Secrets | SSM Parameter Store, SecureString, under `/universal-agents/` | `deploy/env-from-ssm.sh` writes `deploy/.env` from them; never in git |
| Instance role | SSM management, read `/universal-agents/*`, write to the backup bucket | Nothing else |
| Metadata | IMDSv2 only, hop limit 1 | Containers cannot read the server's role |
| Backups | `deploy/backup.sh` daily to an S3 bucket (encrypted, 30-day expiry) | Keys, usage and the OIG list survive a lost server |
| Alarms | Status check alarm with automatic recovery, email through SNS | |

Growth, without code changes: a bigger server (`t4g.xlarge`), a hosted model
(`AGENTS_MODEL_OVERRIDES`), or a GPU server with `deploy/compose.gpu.yml` and
`MODEL_TAG=4b`.

## Step by step on AWS

Run from a computer with the AWS CLI signed in to One Smarter's account (an admin). Every
name below can be changed; keep them the same in all steps.

```sh
export AWS_REGION=us-east-1
export NAME=universal-agents
export BACKUP_BUCKET=onesmarter-universal-agents-backups   # must be unique in S3
export ALARM_EMAIL=ops@example.com                         # who gets alarms
```

**1. Secrets** (each value is a long random string, letters, digits, `-` and `_` only):

```sh
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # run twice: pepper, password
aws ssm put-parameter --type SecureString --name /universal-agents/AGENTS_KEY_PEPPER --value '<pepper>'
aws ssm put-parameter --type SecureString --name /universal-agents/POSTGRES_PASSWORD --value '<password>'
aws ssm put-parameter --type SecureString --name /universal-agents/AGENTS_GITHUB_TOKEN --value '<github_pat_...>'
# when the SAM.gov System Account key arrives:
aws ssm put-parameter --type SecureString --name /universal-agents/AGENTS_SAM_API_KEY --value '<key>'
```

The AWS key pepper is new: keys made on a laptop never work on AWS.

**2. Backup bucket**

```sh
aws s3api create-bucket --bucket "$BACKUP_BUCKET"
aws s3api put-public-access-block --bucket "$BACKUP_BUCKET" --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-encryption --bucket "$BACKUP_BUCKET" --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
aws s3api put-bucket-lifecycle-configuration --bucket "$BACKUP_BUCKET" --lifecycle-configuration \
  '{"Rules":[{"ID":"expire-30-days","Status":"Enabled","Filter":{},"Expiration":{"Days":30}}]}'
```

**3. Server role**

```sh
aws iam create-role --role-name "$NAME" --assume-role-policy-document \
  '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
aws iam attach-role-policy --role-name "$NAME" --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
aws iam put-role-policy --role-name "$NAME" --policy-name agents-own --policy-document "{
  \"Version\":\"2012-10-17\",\"Statement\":[
   {\"Effect\":\"Allow\",\"Action\":[\"ssm:GetParameter\"],\"Resource\":\"arn:aws:ssm:$AWS_REGION:$ACCOUNT:parameter/universal-agents/*\"},
   {\"Effect\":\"Allow\",\"Action\":[\"s3:PutObject\"],\"Resource\":\"arn:aws:s3:::$BACKUP_BUCKET/*\"}]}"
aws iam create-instance-profile --instance-profile-name "$NAME"
aws iam add-role-to-instance-profile --instance-profile-name "$NAME" --role-name "$NAME"
sleep 20   # a new instance profile takes a few seconds to be usable
```

**4. Firewall (security group)** in the default VPC:

```sh
VPC=$(aws ec2 describe-vpcs --filters Name=is-default,Values=true --query 'Vpcs[0].VpcId' --output text)
SG=$(aws ec2 create-security-group --group-name "$NAME" --description "Universal Agents: HTTPS only" \
  --vpc-id "$VPC" --query GroupId --output text)
aws ec2 authorize-security-group-ingress --group-id "$SG" --protocol tcp --port 443 --cidr 0.0.0.0/0
aws ec2 authorize-security-group-ingress --group-id "$SG" --protocol tcp --port 80 --cidr 0.0.0.0/0
```

**5. Server**

```sh
AMI=$(aws ssm get-parameter --name /aws/service/canonical/ubuntu/server/24.04/stable/current/arm64/hvm/ebs-gp3/ami-id \
  --query Parameter.Value --output text)
ID=$(aws ec2 run-instances --image-id "$AMI" --instance-type t4g.large \
  --iam-instance-profile Name="$NAME" --security-group-ids "$SG" \
  --metadata-options HttpTokens=required,HttpPutResponseHopLimit=1,HttpEndpoint=enabled \
  --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":40,"VolumeType":"gp3","Encrypted":true}}]' \
  --credit-specification CpuCredits=unlimited \
  --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$NAME}]" \
  --query 'Instances[0].InstanceId' --output text)
aws ec2 wait instance-running --instance-ids "$ID"
EIP=$(aws ec2 allocate-address --query AllocationId --output text)
aws ec2 associate-address --instance-id "$ID" --allocation-id "$EIP"
aws ec2 describe-addresses --allocation-ids "$EIP" --query 'Addresses[0].PublicIp' --output text
```

Point the DNS name (for example `agents.<domain>`) at that address with an A record.

**6. Alarm:** email on a failed status check, and automatic recovery:

```sh
TOPIC=$(aws sns create-topic --name "$NAME-alarms" --query TopicArn --output text)
aws sns subscribe --topic-arn "$TOPIC" --protocol email --notification-endpoint "$ALARM_EMAIL"   # confirm the email
aws cloudwatch put-metric-alarm --alarm-name "$NAME-status" --namespace AWS/EC2 \
  --metric-name StatusCheckFailed_System --dimensions Name=InstanceId,Value="$ID" \
  --statistic Maximum --period 60 --evaluation-periods 2 --threshold 1 \
  --comparison-operator GreaterThanOrEqualToThreshold \
  --alarm-actions "arn:aws:automate:$AWS_REGION:ec2:recover" "$TOPIC"
```

**7. On the server** (`aws ssm start-session --target "$ID"`, then `sudo -i`; the computer needs
AWS's Session Manager plugin, or use EC2 → Connect → Session Manager in the console):

```sh
apt-get update && apt-get install -y docker.io docker-compose-v2 git curl
snap install aws-cli --classic
git clone -b production-ready https://github.com/OneSmarterInc/Universal_Agents.git /opt/universal-agents
cd /opt/universal-agents
deploy/env-from-ssm.sh API_DOMAIN=agents.<domain> MODEL_TAG=1.7b
docker compose -f deploy/compose.yml --env-file deploy/.env --profile https up -d --build
docker compose -f deploy/compose.yml --env-file deploy/.env logs -f model-init    # wait for "kormic-local ready"
curl -s https://agents.<domain>/health/ready
```

**8. Data, jobs and the first key**

```sh
cd /opt/universal-agents
deploy/oig-refresh.sh
crontab -l 2>/dev/null > /tmp/cron || true
cat >> /tmp/cron <<CRON
17 3 12 * *  /opt/universal-agents/deploy/oig-refresh.sh >> /var/log/oig-refresh.log 2>&1
41 2 * * *   BACKUP_BUCKET=s3://$BACKUP_BUCKET /opt/universal-agents/deploy/backup.sh >> /var/log/agents-backup.log 2>&1
CRON
crontab /tmp/cron
alias agents='docker compose -f deploy/compose.yml --env-file deploy/.env run --rm --no-deps api'
agents model-check
agents create-key --project kormic --agent cv      # give the key to Kormic's backend settings
```

**9. Check:** run one CV, one set of LinkedIn screenshots and one GitHub lookup with a
test key (`agents try --url https://agents.<domain> ...` from a laptop), then revoke it.

## Before production

- [ ] SAM.gov System Account key (`AGENTS_SAM_API_KEY`, `AGENTS_SAM_DAILY_LIMIT`)
- [ ] GitHub token with no scopes (`AGENTS_GITHUB_TOKEN`)
- [ ] `AGENTS_SAM_MAPPING_VERIFIED=true` (checked on the laptop on 23 Oct 2026)
- [ ] A new `AGENTS_KEY_PEPPER` for AWS (laptop keys never work there)
- [ ] OIG list loaded and the monthly refresh in cron
- [ ] Daily backup in cron, and one restore tried
- [ ] Re-run the 7 student CVs and the LinkedIn screenshots on the server's model
- [ ] Restore one backup into a scratch database once
