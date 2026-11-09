#!/usr/bin/env bash
set -euo pipefail

echo "==> Starting The Sommelier..."

# A failed command substitution inside mkdir's arguments does not stop the
# script, and mkdir "" gives an error that does not name the cause.
DATA_DIR=$(python3 /app/scripts/settings.py get data_dir) || { echo "ERROR: settings.py get data_dir failed" >&2; exit 1; }
SITE_DIR=$(python3 /app/scripts/settings.py get site_dir) || { echo "ERROR: settings.py get site_dir failed" >&2; exit 1; }
# nginx.conf has a fixed root for the run output. Another SITE_DIR, or a
# DATA_DIR that moves the default SITE_DIR, gives a site that never shows a new plan.
if [[ "${SITE_DIR}" != "/data/site" ]]; then
  echo "ERROR: SITE_DIR is ${SITE_DIR}. In the container it must be /data/site, because nginx reads the run output only from that folder. Remove SITE_DIR and DATA_DIR from the environment." >&2
  exit 1
fi
mkdir -p "${DATA_DIR}" "${SITE_DIR}" || { echo "ERROR: Cannot create ${DATA_DIR} or ${SITE_DIR}. Check the volume and its owner." >&2; exit 1; }

# Start nginx first so the site is available immediately
echo "==> Starting web server on port 8080..."
# /tmp is a tmpfs at run time, so the nginx temp folder is empty at each start.
mkdir -p /tmp/nginx
nginx

# Set up periodic sync with supercronic (runs as non-root)
# SYNC_SCHEDULE can hold more than one cron expression, separated by ";".
# DIGEST_SEND_SCHEDULE holds one.
# Cron expressions contain spaces and commas, so neither can be the separator.
# settings.py owns the defaults. It stops the start on a value with a newline,
# because each line of the crontab is a command that supercronic runs. It also
# removes each blank entry, so each entry below is a cron expression.
SCHEDULE=$(python3 /app/scripts/settings.py get sync_schedule) || exit 1
DIGEST_SCHEDULE=$(python3 /app/scripts/settings.py get digest_schedule) || exit 1
IFS=';' read -ra SYNC_EXPRS <<< "${SCHEDULE}"
# supercronic does not overlap runs of one job, but the initial sync below is
# not a supercronic job. The shared lock stops a scheduled run that starts
# during the initial sync. A run that cannot get the lock is skipped.
# -o keeps the lock out of child processes. Without it, a child process that
# lives after the run keeps the lock open, and every later run is skipped.
SYNC_LOCK=/tmp/sync.lock
printf '%s cd /app && flock -n -o '"${SYNC_LOCK}"' bash fetch_docker.sh\n' "${SYNC_EXPRS[@]}" > /tmp/crontab
echo "${DIGEST_SCHEDULE} cd /app && bash send_digest.sh" >> /tmp/crontab
echo "    Scheduled sync: ${SCHEDULE}"
echo "    Scheduled digest: ${DIGEST_SCHEDULE}"
# supercronic runs in the background, so a bad line would stop every job with
# no sign here. -test checks the cron expressions before the start.
supercronic -test /tmp/crontab || { echo "ERROR: The crontab is not valid. Check SYNC_SCHEDULE and DIGEST_SEND_SCHEDULE." >&2; exit 1; }
supercronic /tmp/crontab &

# Run the pipeline once (site is already serving)
echo "==> Running initial sync..."
flock -n -o "${SYNC_LOCK}" bash /app/fetch_docker.sh || echo "WARNING: Initial sync failed, serving stale plan"

echo "==> Ready."
tail -f /dev/null
