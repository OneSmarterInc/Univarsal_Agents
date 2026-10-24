#!/bin/sh
# Writes deploy/.env from deploy/env.example and the secrets in SSM Parameter Store.
# Run on the server (it uses the server's role). Parameters, all SecureString:
#   /universal-agents/AGENTS_KEY_PEPPER   /universal-agents/POSTGRES_PASSWORD
#   /universal-agents/AGENTS_SAM_API_KEY  /universal-agents/AGENTS_GITHUB_TOKEN
# Other settings: pass them as NAME=value arguments, for example
#   deploy/env-from-ssm.sh API_DOMAIN=agents.example.com MODEL_TAG=1.7b
set -eu
# cron starts with a short PATH; the AWS CLI may live in /snap/bin.
PATH="$PATH:/usr/local/bin:/snap/bin"
cd "$(dirname "$0")"
PREFIX="${SSM_PREFIX:-/universal-agents}"
umask 077
cp env.example .env.new
set_value() {
  # Replace NAME=... (or add it) with the given value; values never reach the screen.
  name="$1"; value="$2"
  grep -v "^${name}=" .env.new > .env.tmp || true
  printf '%s=%s\n' "$name" "$value" >> .env.tmp
  mv .env.tmp .env.new
}
read_parameter() {
  aws ssm get-parameter --with-decryption --name "$PREFIX/$1" --query Parameter.Value --output text
}
# Required: the service does not start without these.
for name in AGENTS_KEY_PEPPER POSTGRES_PASSWORD; do
  set_value "$name" "$(read_parameter "$name")"
done
# Optional: SAM and GitHub work without them (SAM then reports "needs setup").
for name in AGENTS_SAM_API_KEY AGENTS_GITHUB_TOKEN; do
  if value="$(read_parameter "$name" 2>/dev/null)"; then
    set_value "$name" "$value"
  else
    echo "Note: $PREFIX/$name not found; left empty."
  fi
done
for pair in "$@"; do
  set_value "${pair%%=*}" "${pair#*=}"
done
mv .env.new .env
echo "deploy/.env written ($(grep -c '=' .env) settings)."
