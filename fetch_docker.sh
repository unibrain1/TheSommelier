#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# In Docker, compose gives all settings (env_file and environment:). Do not
# read .env here.
source pipeline.sh

echo "==> Done."
jq '.summary' "${DATA_DIR}/report.json"
echo ""
# pipeline.sh makes no pairing file when the menu feature is off.
if [[ -f "${DATA_DIR}/pairing_suggestions.json" ]]; then
  echo "Pairing summary:"
  jq '{total_meals, matched_weeks, sommelier_picks}' "${DATA_DIR}/pairing_suggestions.json"
else
  echo "Pairing: menu feature is off"
fi
