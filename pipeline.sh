#!/usr/bin/env bash
set -euo pipefail
# Shared pipeline logic — sourced by fetch.sh and fetch_docker.sh
# Expects: working directory set to project root, env vars already loaded

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# --- HEALTHCHECKS.IO ---
HC_PING_SLUG="the-sommelier"
HC_PING_URL=""

if [[ -n "${HEALTHCHECK_PING_KEY:-}" ]]; then
  if HC_RESOLVED=$(op read "${HEALTHCHECK_PING_KEY}" 2>/dev/null); then
    HC_PING_URL="https://hc-ping.com/${HC_RESOLVED}/${HC_PING_SLUG}"
  else
    log "WARNING: Failed to resolve Healthchecks.io ping key from 1Password"
  fi
fi

hc_ping() {
  local status="$1"
  local message="$2"

  if [[ -z "${HC_PING_URL}" ]]; then
    return 0
  fi

  local ping_url="${HC_PING_URL}"
  if [[ "${status}" == "FAILED" ]]; then
    ping_url="${HC_PING_URL}/fail"
  fi

  curl -sf --retry 3 --max-time 5 \
    -X POST \
    --data-raw "${message}" \
    "${ping_url}" >/dev/null 2>&1 || log "WARNING: Failed to send Healthchecks.io ping"
}

# Send failure ping on any non-zero exit
trap 'hc_ping "FAILED" "Pipeline failed. Check container logs for details."' ERR

# --- SELF-UPDATE ---
# Bash keeps reading the copies of this file and the wrapper that it opened at
# start. If the pull brings new commits, start the wrapper again so that the
# new shell code runs. PIPELINE_REEXEC stops a second pull, so the restart
# cannot loop. $0 is the wrapper, because the wrapper sources this file.
if [[ -d .git && -z "${PIPELINE_REEXEC:-}" ]]; then
  PIPELINE_HEAD=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
  log "==> Pulling latest from git..."
  git pull --ff-only 2>&1 || log "WARNING: git pull failed — running with current version"
  if [[ "$(git rev-parse HEAD 2>/dev/null || echo "changed")" != "${PIPELINE_HEAD}" ]]; then
    log "==> New commits pulled, starting the pipeline again..."
    PIPELINE_REEXEC=1 exec bash "$0" "$@"
  fi
fi
unset PIPELINE_REEXEC

# --- FETCH (scripted) ---
log "==> Resolving credentials from 1Password..."
CT_USERNAME=$(op read "$USERNAME") || { log "ERROR: Failed to resolve CellarTracker username from 1Password"; exit 1; }
CT_PASSWORD=$(op read "$PASSWORD") || { log "ERROR: Failed to resolve CellarTracker password from 1Password"; exit 1; }
GOOGLE_CALENDAR_ICS_URL=$(op read "$GOOGLE_CALENDAR_ICS_URL") || { log "ERROR: Failed to resolve Google Calendar URL from 1Password"; exit 1; }
export CLAUDE_CODE_OAUTH_TOKEN
CLAUDE_CODE_OAUTH_TOKEN=$(op read "$CLAUDE_CODE_OAUTH_TOKEN") || { log "WARNING: Failed to resolve Claude OAuth token from 1Password"; }
CT_COMMUNITY_NOTES_RSS=$(op read "${CT_COMMUNITY_NOTES_RSS:-}") || { log "WARNING: Failed to resolve community notes RSS URL from 1Password — continuing without community notes"; CT_COMMUNITY_NOTES_RSS=""; }
export SMTP_USERNAME SMTP_PASSWORD DIGEST_RECIPIENTS
SMTP_USERNAME=$(op read "${SMTP_USERNAME:-}") || { log "WARNING: Failed to resolve SMTP username — digest email disabled"; SMTP_USERNAME=""; }
SMTP_PASSWORD=$(op read "${SMTP_PASSWORD:-}") || { log "WARNING: Failed to resolve SMTP password — digest email disabled"; SMTP_PASSWORD=""; }

CT_BASE="https://www.cellartracker.com/xlquery.asp?User=${CT_USERNAME}&Password=${CT_PASSWORD}&Format=tab"

log "==> Fetching inventory, notes, food tags, and menu..."
curl -sf "${CT_BASE}&Table=Inventory&Location=1" -o data/inventory.tsv &
PID_CT=$!

curl -sf "${CT_BASE}&Table=Notes" -o data/notes.tsv &
PID_NOTES=$!

curl -sf "${CT_BASE}&Table=FoodTags" -o data/foodtags.tsv &
PID_FOOD=$!

curl -sf "${CT_BASE}&Table=Consumed" -o data/consumed.tsv &
PID_CONSUMED=$!

curl -sf "${GOOGLE_CALENDAR_ICS_URL}" -o data/menu.ics &
PID_CAL=$!

wait $PID_CT || { log "ERROR: CellarTracker inventory fetch failed"; exit 1; }
wait $PID_NOTES || { log "WARNING: CellarTracker notes fetch failed — continuing without notes"; }
wait $PID_FOOD || { log "WARNING: CellarTracker food tags fetch failed — continuing without food tags"; }
wait $PID_CONSUMED || { log "WARNING: CellarTracker consumed fetch failed — continuing without consumed data"; }
wait $PID_CAL || { log "ERROR: Google Calendar fetch failed — check GOOGLE_CALENDAR_ICS_URL"; exit 1; }

LINES=$(wc -l < data/inventory.tsv | tr -d ' ')
log "    Downloaded $((LINES - 1)) bottles + notes + food tags + consumed + menu calendar"

# --- PARSE (scripted) ---
log "==> Parsing inventory and menu..."
python3 scripts/parse_inventory.py data/inventory.tsv > data/inventory.json &
PID_INV=$!

python3 scripts/parse_menu.py data/menu.ics > data/menu.json &
PID_MENU=$!

if [[ -s data/consumed.tsv ]]; then
  python3 scripts/parse_consumed.py data/consumed.tsv > data/consumed.json &
  PID_CONSUMED_PARSE=$!
fi

wait $PID_INV || { log "ERROR: Inventory parse failed"; exit 1; }
wait $PID_MENU || { log "ERROR: Menu parse failed"; exit 1; }
if [[ -n "${PID_CONSUMED_PARSE:-}" ]]; then
  wait "$PID_CONSUMED_PARSE" || { log "WARNING: Consumed parse failed — continuing without consumed data"; }
fi

# --- FETCH COMMUNITY NOTES (RSS) ---
if [[ -n "${CT_COMMUNITY_NOTES_RSS:-}" ]]; then
  log "==> Fetching community tasting notes (RSS)..."
  CT_COMMUNITY_NOTES_RSS="${CT_COMMUNITY_NOTES_RSS}" python3 scripts/fetch_community_notes.py data/community_notes.json || log "WARNING: Community notes fetch failed — continuing without community notes"
else
  log "    Skipping community notes — CT_COMMUNITY_NOTES_RSS not configured"
fi

# --- GENERATE PLAN to staging (scripted — rules-based) ---
# Build new plan in data/ first, only publish to site/ when complete with notes
log "==> Generating plan..."
python3 scripts/generate_plan.py data/inventory.json data/plan.json || { log "ERROR: Plan generation failed"; exit 1; }

# --- GENERATE NOTES (LLM — Claude Code CLI, augmented with CT notes) ---
if command -v claude &> /dev/null; then
  log "==> Generating tasting notes (Claude)..."
  python3 scripts/generate_notes.py data/plan.json data/notes.tsv data/foodtags.tsv data/community_notes.json data/inventory.json || log "WARNING: Note generation failed — plan will have empty notes"
else
  log "    Skipping note generation — claude CLI not available"
fi

# --- COMPARE & PAIR (scripted) ---
log "==> Comparing inventory vs plan..."
python3 scripts/compare.py data/inventory.json data/plan.json > data/report.json || { log "ERROR: Compare failed"; exit 1; }

# --- ENRICH MENU (LLM — Claude Code CLI) ---
if command -v claude &> /dev/null; then
  log "==> Enriching menu entries (Claude)..."
  python3 scripts/enrich_menu.py data/menu.json data/menu_enriched.json data/menu_enriched_full.json || log "WARNING: Menu enrichment failed — pairing will use keyword matching only"
else
  log "    Skipping menu enrichment — claude CLI not available"
fi

log "==> Generating pairing suggestions..."
python3 scripts/pairing.py data/menu.json data/plan.json data/inventory.json data/menu_enriched_full.json > data/pairing_suggestions.json || { log "ERROR: Pairing failed"; exit 1; }

# --- PUBLISH atomically (site always has a complete plan with notes) ---
log "==> Publishing to site/..."
cp data/plan.json site/plan.json
cp data/pairing_suggestions.json site/pairing_suggestions.json
cp data/report.json site/report.json

# --- GENERATE HOME ASSISTANT JSON ---
log "==> Generating Home Assistant JSON..."
python3 scripts/thisweek.py data/plan.json > site/thisweek.json || log "WARNING: thisweek.py failed"
python3 scripts/today.py data/pairing_suggestions.json > site/today.json || log "WARNING: today.py failed"

# --- PUSH TO HOME ASSISTANT ---
HA_SSH_KEY="/run/secrets/ha_ssh_key"
if [[ -n "${HA_SSH_TARGET:-}" ]] && [[ -f "${HA_SSH_KEY}" ]]; then
  log "==> Pushing to Home Assistant (${HA_SSH_TARGET})..."
  HA_SSH_OPTS="-i ${HA_SSH_KEY} -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -o BatchMode=yes"
  # shellcheck disable=SC2086
  if ssh ${HA_SSH_OPTS} "${HA_SSH_TARGET}" "sudo tee /config/www/thisweek.json > /dev/null" < site/thisweek.json \
    && ssh ${HA_SSH_OPTS} "${HA_SSH_TARGET}" "sudo tee /config/www/today.json > /dev/null" < site/today.json; then
    log "    Pushed thisweek.json + today.json to Home Assistant"
  else
    log "WARNING: Failed to push JSON to Home Assistant"
  fi
else
  log "    Skipping Home Assistant push — HA_SSH_TARGET or SSH key not configured"
fi

# --- GENERATE DIGEST ---
log "==> Generating morning digest..."
python3 scripts/generate_digest.py || { RC=$?; if [ "$RC" -eq 2 ]; then log "    Digest: nothing to send (routine day)"; else log "WARNING: Digest generation failed"; fi; }

log "==> Sync complete."

# --- HEALTHCHECK PING (success) ---
BOTTLES=$(python3 -c "import json; d=json.load(open('data/report.json')); print(d['summary']['inventory_total_bottles'])" 2>/dev/null || echo "?")
PLANNED=$(python3 -c "import json; d=json.load(open('data/plan.json')); print(len(d['allWeeks']))" 2>/dev/null || echo "?")
hc_ping "SUCCESS" "Pipeline completed successfully.

Bottles: ${BOTTLES}
Weeks planned: ${PLANNED}
Time: $(date '+%Y-%m-%d %H:%M:%S')"
