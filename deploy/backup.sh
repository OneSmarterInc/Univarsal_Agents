#!/bin/sh
# Daily database backup (keys, usage, OIG list) to S3. Uploaded files and results are
# temporary (7 days) and are not backed up. Example cron line:
#   41 2 * * *  /opt/universal-agents/deploy/backup.sh >> /var/log/agents-backup.log 2>&1
set -eu
cd "$(dirname "$0")/.."
: "${BACKUP_BUCKET:?set BACKUP_BUCKET, for example s3://example-agents-backups}"
STAMP="$(date -u +%Y-%m-%dT%H%M%SZ)"
docker compose -f deploy/compose.yml --env-file deploy/.env exec -T postgres \
  pg_dump -U agents -d agents --format=custom \
  | aws s3 cp - "$BACKUP_BUCKET/agents-$STAMP.dump" --sse AES256
echo "backup agents-$STAMP.dump done"
