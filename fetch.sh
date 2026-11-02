#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# Skip .env when all three required values are already in the environment. Then
# the 1Password CLI can start this script with resolved values (its `run`
# command with --env-file=.env), and the op:// references in .env do not replace
# them. All three, because a shell can set USERNAME for its own use.
if [[ -f .env && ( -z "${USERNAME:-}" || -z "${PASSWORD:-}" || -z "${GOOGLE_CALENDAR_ICS_URL:-}" ) ]]; then
  set -a
  source .env
  set +a
fi

export DATA_DIR="${DATA_DIR:-$PWD/data}" SITE_DIR="${SITE_DIR:-$PWD/site}"
export PLAN_CONFIG="${PLAN_CONFIG:-$PWD/config/plan.yaml}"

source pipeline.sh

echo "==> Done."
echo ""
jq '.summary' "${DATA_DIR}/report.json"
echo ""
echo "Pairing summary:"
jq '{total_meals, matched_weeks, sommelier_picks}' "${DATA_DIR}/pairing_suggestions.json"
