#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# Skip .env when the values are already in the environment (compose env_file
# or the 1Password CLI), so that the op:// references in .env do not replace
# the resolved values.
if [[ -f .env && -z "${SMTP_USERNAME:-}" ]]; then
  set -a
  source .env
  set +a
fi

# A .env file means a run from a repo checkout, so use the repo folders, as
# fetch.sh does. The standalone image has no .env, so it keeps the /data default.
if [[ -f .env ]]; then
  export DATA_DIR="${DATA_DIR:-$PWD/data}" SITE_DIR="${SITE_DIR:-$PWD/site}"
fi

python3 scripts/send_digest.py "$@"
