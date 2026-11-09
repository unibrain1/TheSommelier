#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# supercronic does not overlap runs of one job, but the initial sync is not a
# supercronic job. The shared lock stops a scheduled run that starts during
# the initial sync. A run that cannot get the lock is skipped.
# -o keeps the lock out of child processes. Without it, a child process that
# lives after the run keeps the lock open, and every later run is skipped.
# -E 75 gives a skipped run its own exit code. By default flock exits 1 on a
# conflict, which is the same code as a failed run.
rc=0
flock -n -E 75 -o /tmp/sync.lock bash fetch_docker.sh || rc=$?

if [[ ${rc} -eq 75 ]]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Skipping sync: another sync holds /tmp/sync.lock"
  exit 0
fi
exit "${rc}"
