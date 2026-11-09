#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# A run on the host uses the repo folders, as fetch.sh does. Docker creates
# /.dockerenv and Podman creates /run/.containerenv (settings.CONTAINER_MARKERS).
# In the container, compose gives all settings. The image has no .env and the
# repo is not mounted, so the host defaults must not apply there.
if [[ ! -f /.dockerenv && ! -f /run/.containerenv ]]; then
  # Skip .env when both required values are already in the environment. Then
  # the 1Password CLI can start this script with resolved values, and the
  # op:// references in .env do not replace them. fetch.sh uses the same rule
  # for USERNAME and PASSWORD.
  if [[ -f .env && ( -z "${SMTP_USERNAME:-}" || -z "${SMTP_PASSWORD:-}" ) ]]; then
    set -a
    source .env
    set +a
  fi
  export DATA_DIR="${DATA_DIR:-$PWD/data}" SITE_DIR="${SITE_DIR:-$PWD/site}"
fi

rc=0
python3 scripts/send_digest.py "$@" || rc=$?

# Only a run with no arguments (the scheduled run) pings. A manual run with
# --dry-run or --force must not mark the check as down. Exit with the same code so supercronic still
# logs the failed job.
if [[ $# -eq 0 && ${rc} -ne 0 ]]; then
  message="Digest send failed. Check container logs for details."
  if [[ ${rc} -eq 3 ]]; then
    message="Digest not sent: digest.json is not for today. The last sync did not finish, or it did not make a new digest."
  fi
  python3 scripts/healthcheck.py fail "${message}"
fi
exit "${rc}"
