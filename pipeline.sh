#!/usr/bin/env bash
set -euo pipefail
# Shared pipeline logic — sourced by fetch.sh and fetch_docker.sh
# Expects: working directory set to project root, env vars already loaded

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# Send failure ping on any non-zero exit. EXIT, not ERR: each step stops the
# run with its own `|| { ...; exit 1; }`, and an explicit exit does not fire ERR.
ping_on_failure() {
  local rc=$?
  if [[ ${rc} -ne 0 ]]; then
    python3 scripts/healthcheck.py fail "Pipeline failed. Check container logs for details."
  fi
}
trap ping_on_failure EXIT

# --- SETTINGS ---
log "==> Checking settings..."
python3 scripts/settings.py check || { log "ERROR: Settings check failed"; exit 1; }
DATA_DIR=$(python3 scripts/settings.py get data_dir)
SITE_DIR=$(python3 scripts/settings.py get site_dir)
mkdir -p "${DATA_DIR}" "${SITE_DIR}"
# A run that was killed in publish_ha_json leaves its temporary file here.
rm -f "${SITE_DIR}"/.thisweek.json.* "${SITE_DIR}"/.today.json.*

# --- FETCH (scripted) ---
CURL_TIMEOUTS=(--connect-timeout 15 --max-time 300)
# -S logs the cause of a failure (DNS, timeout, HTTP code). The message names
# the host, not the query, so no secret goes into the log.

# A secret goes to curl in a config on stdin, not in argv, where `ps` shows
# it. Only builtins (printf, parameter expansion) touch it, so no child process
# gets it as an argument. A quoted config value reads \\ and \" as escapes.
# A line break would end the config line. settings.py check stops the run when
# USERNAME or PASSWORD holds one. It treats a calendar URL with one as not
# valid, so the menu is off and there is no fetch.
curl_quote() { local v=${1//\\/\\\\}; printf '"%s"' "${v//\"/\\\"}"; }

# $1 = Table value, $2 = output file, $3 = an optional extra query parameter.
# curl URL-encodes User and Password, so a value is plain text, not encoded.
ct_fetch() {
  local extra=""
  [[ -n "${3:-}" ]] && extra="data = \"$3\""$'\n'
  printf 'url = "https://www.cellartracker.com/xlquery.asp"\nget\ndata = "Format=tab"\ndata = "Table=%s"\n%sdata-urlencode = %s\ndata-urlencode = %s\n' \
    "$1" "${extra}" "$(curl_quote "User=${USERNAME}")" "$(curl_quote "Password=${PASSWORD}")" \
    | curl -sSf "${CURL_TIMEOUTS[@]}" -K - -o "$2"
}

MENU_ON=0
MENU_FETCH_LOG="" MENU_DOWNLOAD_LOG="" MENU_PARSE_LOG=""
if python3 scripts/settings.py enabled menu; then
  MENU_ON=1
  MENU_FETCH_LOG=", and menu" MENU_DOWNLOAD_LOG=" + menu calendar" MENU_PARSE_LOG=" and menu"
fi

log "==> Fetching inventory, notes, food tags${MENU_FETCH_LOG}..."
ct_fetch Inventory "${DATA_DIR}/inventory.tsv" "Location=1" &
PID_CT=$!

ct_fetch Notes "${DATA_DIR}/notes.tsv" &
PID_NOTES=$!

ct_fetch FoodTags "${DATA_DIR}/foodtags.tsv" &
PID_FOOD=$!

ct_fetch Consumed "${DATA_DIR}/consumed.tsv" &
PID_CONSUMED=$!

if [[ ${MENU_ON} -eq 1 ]]; then
  # The URL is a secret, so it goes on stdin too. globoff: curl must not read
  # {} or [] in the URL as a glob.
  printf 'url = %s\ngloboff\n' "$(curl_quote "${GOOGLE_CALENDAR_ICS_URL}")" \
    | curl -sSf "${CURL_TIMEOUTS[@]}" -K - -o "${DATA_DIR}/menu.ics" &
  PID_CAL=$!
else
  # Files from an earlier run would give pairings for a menu that is gone. A
  # lost setting (a typo, a dropped env_file line) would remove the menu with
  # only an info line in the log, so a removal of published pairings warns.
  if [[ -f "${SITE_DIR}/pairing_suggestions.json" ]]; then
    log "WARNING: menu feature is off — removing the pairings of an earlier run"
  fi
  rm -f "${DATA_DIR}/menu.ics" "${DATA_DIR}/menu.json" "${DATA_DIR}/pairing_suggestions.json" "${SITE_DIR}/pairing_suggestions.json"
  log "    Skipping menu calendar — menu feature is off"
fi

wait $PID_CT || { log "ERROR: CellarTracker inventory fetch failed"; exit 1; }
wait $PID_NOTES || { log "WARNING: CellarTracker notes fetch failed — continuing without notes"; }
wait $PID_FOOD || { log "WARNING: CellarTracker food tags fetch failed — continuing without food tags"; }
wait $PID_CONSUMED || { log "WARNING: CellarTracker consumed fetch failed — continuing without consumed data"; }
if [[ ${MENU_ON} -eq 1 ]]; then
  wait "$PID_CAL" || { log "ERROR: Google Calendar fetch failed — check GOOGLE_CALENDAR_ICS_URL"; exit 1; }
fi

LINES=$(wc -l < "${DATA_DIR}/inventory.tsv" | tr -d ' ')
log "    Downloaded $((LINES - 1)) bottles + notes + food tags + consumed${MENU_DOWNLOAD_LOG}"

# --- PARSE (scripted) ---
log "==> Parsing inventory${MENU_PARSE_LOG}..."
python3 scripts/parse_inventory.py "${DATA_DIR}/inventory.tsv" > "${DATA_DIR}/inventory.json" &
PID_INV=$!

if [[ ${MENU_ON} -eq 1 ]]; then
  python3 scripts/parse_menu.py "${DATA_DIR}/menu.ics" > "${DATA_DIR}/menu.json" &
  PID_MENU=$!
fi

if [[ -s "${DATA_DIR}/consumed.tsv" ]]; then
  python3 scripts/parse_consumed.py "${DATA_DIR}/consumed.tsv" > "${DATA_DIR}/consumed.json" &
  PID_CONSUMED_PARSE=$!
fi

wait $PID_INV || { log "ERROR: Inventory parse failed"; exit 1; }
if [[ ${MENU_ON} -eq 1 ]]; then
  wait "$PID_MENU" || { log "ERROR: Menu parse failed"; exit 1; }
fi
if [[ -n "${PID_CONSUMED_PARSE:-}" ]]; then
  wait "$PID_CONSUMED_PARSE" || { log "WARNING: Consumed parse failed — continuing without consumed data"; }
fi

# --- FETCH COMMUNITY NOTES (RSS) ---
if python3 scripts/settings.py enabled community_notes; then
  log "==> Fetching community tasting notes (RSS)..."
  python3 scripts/fetch_community_notes.py "${DATA_DIR}/community_notes.json" || log "WARNING: Community notes fetch failed — continuing without community notes"
else
  log "    Skipping community notes — community notes feature is off"
fi

# --- GENERATE PLAN to staging (scripted — rules-based) ---
# Build new plan in DATA_DIR first, only publish to SITE_DIR when complete with notes
log "==> Generating plan..."
python3 scripts/generate_plan.py "${DATA_DIR}/inventory.json" "${DATA_DIR}/plan.json" || { log "ERROR: Plan generation failed"; exit 1; }

# --- GENERATE NOTES (LLM — Claude Code CLI, augmented with CT notes) ---
CLAUDE_ON=0
python3 scripts/settings.py enabled claude && CLAUDE_ON=1
if [[ ${CLAUDE_ON} -eq 0 ]]; then
  log "    Skipping note generation — Claude feature is off"
elif ! command -v claude &> /dev/null; then
  log "    Skipping note generation — claude CLI not available"
else
  log "==> Generating tasting notes (Claude)..."
  python3 scripts/generate_notes.py "${DATA_DIR}/plan.json" "${DATA_DIR}/notes.tsv" "${DATA_DIR}/foodtags.tsv" "${DATA_DIR}/community_notes.json" "${DATA_DIR}/inventory.json" || log "WARNING: Note generation failed — plan will have empty notes"
fi

# --- COMPARE & PAIR (scripted) ---
log "==> Comparing inventory vs plan..."
python3 scripts/compare.py "${DATA_DIR}/inventory.json" "${DATA_DIR}/plan.json" > "${DATA_DIR}/report.json" || { log "ERROR: Compare failed"; exit 1; }

# --- ENRICH MENU (LLM — Claude Code CLI) ---
# With the menu off there is no menu.json, so enrichment and pairing have no
# input. The fetch step logs the skip line.
if [[ ${MENU_ON} -eq 1 ]]; then
  if [[ ${CLAUDE_ON} -eq 0 ]]; then
    log "    Skipping menu enrichment — Claude feature is off"
  elif ! command -v claude &> /dev/null; then
    log "    Skipping menu enrichment — claude CLI not available"
  else
    log "==> Enriching menu entries (Claude)..."
    python3 scripts/enrich_menu.py "${DATA_DIR}/menu.json" "${DATA_DIR}/menu_enriched.json" "${DATA_DIR}/menu_enriched_full.json" || log "WARNING: Menu enrichment failed — pairing will use keyword matching only"
  fi

  log "==> Generating pairing suggestions..."
  python3 scripts/pairing.py "${DATA_DIR}/menu.json" "${DATA_DIR}/plan.json" "${DATA_DIR}/inventory.json" "${DATA_DIR}/menu_enriched_full.json" > "${DATA_DIR}/pairing_suggestions.json" || { log "ERROR: Pairing failed"; exit 1; }
fi

# --- PUBLISH atomically (site always has a complete plan with notes) ---
log "==> Publishing to ${SITE_DIR}..."
# container_health.py reads the mtime of the published plan.json as the time of
# the last complete run. Do not use cp -p or touch -r: they keep the old mtime.
cp "${DATA_DIR}/plan.json" "${SITE_DIR}/plan.json"
if [[ ${MENU_ON} -eq 1 ]]; then
  cp "${DATA_DIR}/pairing_suggestions.json" "${SITE_DIR}/pairing_suggestions.json"
fi
cp "${DATA_DIR}/report.json" "${SITE_DIR}/report.json"

# --- HOME ASSISTANT JSON (Home Assistant pulls it with a REST sensor) ---
# A script that fails must not leave an empty or partial file at the public URL.
# On failure the published file is removed, so the sensor gets a 404 and shows
# unavailable. The move is atomic because the temporary file is in SITE_DIR.
# $1 = script, $2 = input file, $3 = name of the published file.
publish_ha_json() {
  local tmp rc=0
  tmp=$(mktemp "${SITE_DIR}/.$3.XXXXXX")
  python3 "scripts/$1" "$2" > "${tmp}" || rc=$?
  if [[ ${rc} -eq 0 ]]; then
    # mktemp creates mode 0600, and the web server user must read the file.
    chmod 644 "${tmp}"
    mv -f "${tmp}" "${SITE_DIR}/$3"
  else
    rm -f "${tmp}" "${SITE_DIR}/$3"
    log "WARNING: $1 failed (exit ${rc}) — removed $3"
  fi
}

if python3 scripts/settings.py enabled home_assistant; then
  log "==> Generating Home Assistant JSON..."
  publish_ha_json thisweek.py "${DATA_DIR}/plan.json" thisweek.json
  publish_ha_json today.py "${DATA_DIR}/pairing_suggestions.json" today.json
else
  # A copy from an earlier run would look current to a sensor that still polls.
  rm -f "${SITE_DIR}/thisweek.json" "${SITE_DIR}/today.json"
  log "    Skipping Home Assistant JSON — HA_ENABLED is not true"
fi

# --- GENERATE DIGEST ---
log "==> Generating morning digest..."
python3 scripts/generate_digest.py || { RC=$?; if [ "$RC" -eq 2 ]; then log "    Digest: nothing to send (routine day)"; else log "WARNING: Digest generation failed"; fi; }

log "==> Sync complete."

# --- HEALTHCHECK PING (success) ---
BOTTLES=$(python3 -c "import json, sys; d=json.load(open(sys.argv[1])); print(d['summary']['inventory_total_bottles'])" "${DATA_DIR}/report.json" 2>/dev/null || echo "?")
PLANNED=$(python3 -c "import json, sys; d=json.load(open(sys.argv[1])); print(len(d['allWeeks']))" "${DATA_DIR}/plan.json" 2>/dev/null || echo "?")
# The wrapper that sources this file runs more commands after this point. A
# failure there must not send a fail ping after the success ping.
trap - EXIT
python3 scripts/healthcheck.py success "Pipeline completed successfully.

Bottles: ${BOTTLES}
Weeks planned: ${PLANNED}
Time: $(date '+%Y-%m-%d %H:%M:%S')"
