#!/usr/bin/env bash
set -euo pipefail
# Shared pipeline logic — sourced by fetch.sh and fetch_docker.sh
# Expects: working directory set to project root, env vars already loaded

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# A degraded step logs a warning and the run goes on. The success ping counts
# and lists the warnings, so a green ping does not hide a degraded run.
# A string, not an array: bash 3.2 (macOS) fails on an empty array under set -u.
WARNINGS=0
WARNING_TEXT=""
add_warning() {
  WARNINGS=$((WARNINGS + 1))
  WARNING_TEXT+=$'\n'"- $1"
}
warn() {
  add_warning "WARNING: $*"
  log "WARNING: $*"
}

# The fail ping names the step. Each name is fixed text, so no secret goes
# into the ping. A step that warns and goes on shows only after a crash or a
# signal, because its own failures do not stop the run.
STEP="settings"

# Send failure ping on any non-zero exit. EXIT, not ERR: each step stops the
# run with its own `|| { ...; exit 1; }`, and an explicit exit does not fire ERR.
ping_on_failure() {
  local rc=$?
  if [[ ${rc} -ne 0 ]]; then
    python3 scripts/healthcheck.py fail "Pipeline failed at step: ${STEP}. Check container logs for details."
  fi
}
trap ping_on_failure EXIT

# --- SETTINGS ---
log "==> Checking settings..."
# The settings check writes its warnings and errors to stderr. Keep that text,
# print it again, and count the WARNING: lines. No settings line holds a value
# (settings.check_lines).
SETTINGS_RC=0
{ SETTINGS_ERR=$(python3 scripts/settings.py check 2>&1 >&3) || SETTINGS_RC=$?; } 3>&1
if [[ -n "${SETTINGS_ERR}" ]]; then
  printf '%s\n' "${SETTINGS_ERR}" >&2
fi
while IFS= read -r line; do
  if [[ "${line}" == WARNING:* ]]; then
    add_warning "${line}"
  fi
done <<< "${SETTINGS_ERR}"
[[ ${SETTINGS_RC} -eq 0 ]] || { log "ERROR: Settings check failed"; exit 1; }
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

STEP="fetch"
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
    warn "menu feature is off — removing the pairings of an earlier run"
  fi
  rm -f "${DATA_DIR}/menu.ics" "${DATA_DIR}/menu.json" "${DATA_DIR}/pairing_suggestions.json" "${SITE_DIR}/pairing_suggestions.json"
  log "    Skipping menu calendar — menu feature is off"
fi

wait $PID_CT || { log "ERROR: CellarTracker inventory fetch failed"; exit 1; }
wait $PID_NOTES || warn "CellarTracker notes fetch failed — continuing without notes"
wait $PID_FOOD || warn "CellarTracker food tags fetch failed — continuing without food tags"
wait $PID_CONSUMED || warn "CellarTracker consumed fetch failed — continuing without consumed data"
if [[ ${MENU_ON} -eq 1 ]]; then
  wait "$PID_CAL" || { log "ERROR: Google Calendar fetch failed — check GOOGLE_CALENDAR_ICS_URL"; exit 1; }
fi

LINES=$(wc -l < "${DATA_DIR}/inventory.tsv" | tr -d ' ')
log "    Downloaded $((LINES - 1)) bottles + notes + food tags + consumed${MENU_DOWNLOAD_LOG}"

# --- PARSE (scripted) ---
STEP="parse"
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
  wait "$PID_CONSUMED_PARSE" || warn "Consumed parse failed — continuing without consumed data"
fi

# --- FETCH COMMUNITY NOTES (RSS) ---
STEP="community notes"
if python3 scripts/settings.py enabled community_notes; then
  log "==> Fetching community tasting notes (RSS)..."
  python3 scripts/fetch_community_notes.py "${DATA_DIR}/community_notes.json" || warn "Community notes fetch failed — continuing without community notes"
else
  log "    Skipping community notes — community notes feature is off"
fi

# --- GENERATE PLAN to staging (scripted — rules-based) ---
# Build new plan in DATA_DIR first, only publish to SITE_DIR when the run completes
STEP="plan"
log "==> Generating plan..."
# Exit 3: the plan is written, but the live plan was bad. generate_plan.py
# names the cause.
PLAN_RC=0
python3 scripts/generate_plan.py "${DATA_DIR}/inventory.json" "${DATA_DIR}/plan.json" || PLAN_RC=$?
if [[ ${PLAN_RC} -eq 3 ]]; then
  warn "Plan generation could not use the previous plan — changelog history or reused notes lost"
elif [[ ${PLAN_RC} -ne 0 ]]; then
  log "ERROR: Plan generation failed"
  exit 1
fi

# --- GENERATE NOTES (LLM — Claude Code CLI, augmented with CT notes) ---
STEP="notes"
CLAUDE_ON=0
python3 scripts/settings.py enabled claude && CLAUDE_ON=1
if [[ ${CLAUDE_ON} -eq 0 ]]; then
  log "    Skipping note generation — Claude feature is off"
elif ! command -v claude &> /dev/null; then
  # The feature is on, so the operator expects notes.
  warn "Skipping note generation — claude CLI not available"
else
  log "==> Generating tasting notes (Claude)..."
  # Exit 3: some weeks have no note. generate_notes.py names them.
  NOTES_RC=0
  python3 scripts/generate_notes.py "${DATA_DIR}/plan.json" "${DATA_DIR}/notes.tsv" "${DATA_DIR}/foodtags.tsv" "${DATA_DIR}/community_notes.json" "${DATA_DIR}/inventory.json" || NOTES_RC=$?
  if [[ ${NOTES_RC} -eq 3 ]]; then
    warn "Note generation incomplete — some new weeks have no note"
  elif [[ ${NOTES_RC} -ne 0 ]]; then
    warn "Note generation failed — new weeks have no note"
  fi
fi

# --- COMPARE & PAIR (scripted) ---
STEP="compare"
log "==> Comparing inventory vs plan..."
python3 scripts/compare.py "${DATA_DIR}/inventory.json" "${DATA_DIR}/plan.json" > "${DATA_DIR}/report.json" || { log "ERROR: Compare failed"; exit 1; }

# --- ENRICH MENU (LLM — Claude Code CLI) ---
# With the menu off there is no menu.json, so enrichment and pairing have no
# input. The fetch step logs the skip line.
if [[ ${MENU_ON} -eq 1 ]]; then
  STEP="menu enrichment"
  if [[ ${CLAUDE_ON} -eq 0 ]]; then
    log "    Skipping menu enrichment — Claude feature is off"
  elif ! command -v claude &> /dev/null; then
    warn "Skipping menu enrichment — claude CLI not available"
  else
    log "==> Enriching menu entries (Claude)..."
    # Exit 3: some menu entries are not enriched. enrich_menu.py counts them.
    ENRICH_RC=0
    python3 scripts/enrich_menu.py "${DATA_DIR}/menu.json" "${DATA_DIR}/menu_enriched.json" "${DATA_DIR}/menu_enriched_full.json" || ENRICH_RC=$?
    if [[ ${ENRICH_RC} -eq 3 ]]; then
      warn "Menu enrichment incomplete — some meals use keyword matching only"
    elif [[ ${ENRICH_RC} -ne 0 ]]; then
      warn "Menu enrichment failed — pairing will use keyword matching only"
    fi
  fi

  STEP="pairing"
  log "==> Generating pairing suggestions..."
  python3 scripts/pairing.py "${DATA_DIR}/menu.json" "${DATA_DIR}/plan.json" "${DATA_DIR}/inventory.json" "${DATA_DIR}/menu_enriched_full.json" > "${DATA_DIR}/pairing_suggestions.json" || { log "ERROR: Pairing failed"; exit 1; }
fi

# --- PUBLISH atomically (site always has the plan of a complete run) ---
STEP="publish"
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
    warn "$1 failed (exit ${rc}) — removed $3"
  fi
}

STEP="home assistant"
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
STEP="digest"
log "==> Generating morning digest..."
python3 scripts/generate_digest.py || { RC=$?; if [ "$RC" -eq 2 ]; then log "    Digest: nothing to send (routine day)"; else warn "Digest generation failed"; fi; }

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
Warnings: ${WARNINGS}${WARNING_TEXT}
Time: $(date '+%Y-%m-%d %H:%M:%S')"
