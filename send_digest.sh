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
# Only a run with no arguments (the scheduled run) pings. A manual run with
# --dry-run or --force must not change the check. send_digest.py writes the
# ping text for a partial refusal to this file, because only it knows the
# counts.
scheduled=0
args=("$@")
if [[ $# -eq 0 ]]; then
  scheduled=1
  message_file="$(mktemp)"
  trap 'rm -f "${message_file}"' EXIT
  args=(--ping-message-file "${message_file}")
fi
python3 scripts/send_digest.py "${args[@]}" || rc=$?

# With HEALTHCHECK_DIGEST_URL, --digest pings the digest check, so a success
# clears a fail state at the next send. Without it, healthcheck.py sends a
# fail to HEALTHCHECK_URL and ignores a success, because the sync owns that
# check. Exit with the same code so supercronic still logs the failed job.
if [[ ${scheduled} -eq 1 ]]; then
  if [[ ${rc} -eq 0 ]]; then
    python3 scripts/healthcheck.py --digest success "Digest send complete."
  else
    message="Digest send failed. Check container logs for details."
    if [[ ${rc} -eq 3 ]]; then
      message="Digest not sent: digest.json is not for today. The last sync did not finish, or it did not make a new digest."
    elif [[ ${rc} -eq 4 ]]; then
      message="$(cat "${message_file}" 2>/dev/null || true)"
      if [[ -z "${message}" ]]; then
        message="Digest sent, but the SMTP server refused some recipients. Check container logs for details."
      fi
    fi
    python3 scripts/healthcheck.py --digest fail "${message}"
  fi
fi
exit "${rc}"
