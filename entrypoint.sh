#!/usr/bin/env bash
set -euo pipefail

echo "==> Starting The Sommelier..."

# Self-update from git
git config --global --add safe.directory /app
echo "==> Pulling latest from git..."
git -C /app pull --ff-only 2>&1 || echo "WARNING: git pull failed — running with current version"

# Install/update Python dependencies if requirements changed
pip install --no-cache-dir --root-user-action=ignore -q -r /app/requirements.txt 2>&1 || true

# Link nginx config from the repo
ln -sf /app/nginx.conf /etc/nginx/sites-available/default

# Ensure data directory exists
mkdir -p /app/data

# Start nginx first so the site is available immediately
echo "==> Starting web server on port 8080..."
nginx

# Set up periodic sync with supercronic (runs as non-root)
# SYNC_SCHEDULE can hold more than one cron expression, separated by ";".
# Cron expressions contain spaces and commas, so neither can be the separator.
# One expression cannot give "every 30 min from 06:00 to 22:00" with the 22:00 run included.
DEFAULT_SCHEDULE="*/30 6-21 * * *;0 22 * * *"
SCHEDULE="${SYNC_SCHEDULE:-${DEFAULT_SCHEDULE}}"
DIGEST_SCHEDULE="${DIGEST_SEND_SCHEDULE:-0 8 * * *}"
# A blank entry (for example "a;;b" or " ") gives a crontab line with no schedule,
# and supercronic then rejects the whole file, digest line included.
SYNC_EXPRS=()
IFS=';' read -ra RAW_EXPRS <<< "${SCHEDULE}"
for expr in "${RAW_EXPRS[@]}"; do
  [[ -n "${expr// /}" ]] && SYNC_EXPRS+=("${expr}")
done
if [[ ${#SYNC_EXPRS[@]} -eq 0 ]]; then
  echo "WARNING: SYNC_SCHEDULE has no cron expression — using default '${DEFAULT_SCHEDULE}'" >&2
  SCHEDULE="${DEFAULT_SCHEDULE}"
  IFS=';' read -ra SYNC_EXPRS <<< "${SCHEDULE}"
fi
# supercronic does not overlap runs of one job, but the initial sync below is
# not a supercronic job. The shared lock stops a scheduled run that starts
# during the initial sync. A run that cannot get the lock is skipped.
SYNC_LOCK=/tmp/sync.lock
printf '%s cd /app && flock -n '"${SYNC_LOCK}"' bash fetch_docker.sh\n' "${SYNC_EXPRS[@]}" > /tmp/crontab
echo "${DIGEST_SCHEDULE} cd /app && bash send_digest.sh" >> /tmp/crontab
echo "    Scheduled sync: ${SCHEDULE}"
echo "    Scheduled digest: ${DIGEST_SCHEDULE}"
supercronic /tmp/crontab &

# Run the pipeline once (site is already serving)
echo "==> Running initial sync..."
flock -n "${SYNC_LOCK}" bash /app/fetch_docker.sh || echo "WARNING: Initial sync failed, serving stale plan"

echo "==> Ready."
tail -f /dev/null
