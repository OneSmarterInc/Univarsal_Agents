#!/bin/sh
# Loads the newest HHS-OIG exclusion list (LEIE) into the OIG agent.
# OIG publishes UPDATED.csv every month. Run monthly, for example from cron:
#   17 3 12 * *  /opt/universal-agents/deploy/oig-refresh.sh >> /var/log/oig-refresh.log 2>&1
set -eu
# cron starts with a short PATH; the AWS CLI may live in /snap/bin.
PATH="$PATH:/usr/local/bin:/snap/bin"
cd "$(dirname "$0")/.."
URL="${OIG_URL:-https://oig.hhs.gov/exclusions/downloadables/UPDATED.csv}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
# -R keeps the server's date on the file: that is the date OIG published it.
curl -fsSL -R --max-time 300 -o "$TMP/UPDATED.csv" "$URL"
AS_OF="$(date -u -r "$TMP/UPDATED.csv" +%Y-%m-%d)"
chmod 644 "$TMP/UPDATED.csv"
chmod 755 "$TMP"
docker compose -f deploy/compose.yml --env-file deploy/.env run --rm --no-deps \
  -v "$TMP:/import:ro" api tool oig load --file /import/UPDATED.csv --as-of "$AS_OF"
