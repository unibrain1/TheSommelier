#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# A run on the host uses the repo folders, as fetch.sh does. Docker creates
# /.dockerenv. In the container, compose gives all settings, and a .env from
# the mounted repo must not change the folders.
if [[ ! -f /.dockerenv ]]; then
  # Skip .env when the values are already in the environment (the 1Password
  # CLI), so that the op:// references in .env do not replace them.
  if [[ -f .env && -z "${SMTP_USERNAME:-}" ]]; then
    set -a
    source .env
    set +a
  fi
  export DATA_DIR="${DATA_DIR:-$PWD/data}" SITE_DIR="${SITE_DIR:-$PWD/site}"
fi

python3 scripts/send_digest.py "$@"
