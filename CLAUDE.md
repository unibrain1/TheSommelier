# CLAUDE.md

## Project Overview

Personal wine cellar management project. Maintains a multi-year wine drinking plan synchronized with a live CellarTracker inventory and a Google Calendar menu plan. A scheduled pipeline (every 30 minutes, 06:00–22:00 Pacific) generates a fresh plan, adds tasting notes via Claude, and publishes a web app.

The flow of a sync is in `docs/how-it-works.md`. The user docs start at `README.md`. `plans/` and `data/` are gitignored.

## Running the Pipeline

```bash
bash fetch.sh
```

## Running Tests

Tests require `pytest` (install via `pip install pytest`).

## Docker Deployment

Production runs in Docker on the owner's homelab server. The commands to restart, rebuild, or read logs on that server are in a local `deploy` skill. That skill is not in git.

Two compose files:

- `examples/compose.yaml` is for a user with no checkout. It uses the image `ghcr.io/unibrain1/thesommelier:4.0` and lists every compose option and every env variable with a comment. A test keeps its variable list in step with the code.
- `docker-compose.yml` builds the image from a checkout (`build: .`). It is generic. Host settings (DNS, port, Traefik labels and network) go in the gitignored `docker-compose.override.yml`. The sample is `docker-compose.override.yml.sample`. On the production server, the HomeLab Ansible deploy writes the override. It must never write the tracked `docker-compose.yml`.

`.github/workflows/publish-image.yml` builds the image for amd64 and arm64. A `v*` tag pushes `X.Y.Z`, `X.Y` and `latest`. A push to `main` or to a `milestone/**` branch pushes `edge`. A manual run needs the workflow file on `main`, and a manual run on a branch also pushes `edge`. A pull request that changes an image file builds only. A prerelease tag (with `-`) does not push `latest`. The GHCR package stays private until the v4.0.0 release. Then the owner sets it to public in the package settings.

The morning digest embeds `site/logo.png`. The script looks for it in the code folder, not in `SITE_DIR`. If the file is missing, the digest uses `SITE_URL` + `/logo.png`. If `SITE_URL` is not set either, the digest has no logo and logs a warning. `SITE_URL` is optional and goes in `.env`.

The image holds the code. The container runs no `git` and no `pip install`. It does not update itself.
A code change needs a new image. With the published image, run `docker compose pull`, then `docker compose down` and `up`. From a checkout, rebuild with `docker compose build`, then `docker compose down` and `up`.
`docker compose restart` does NOT re-run the entrypoint — use `down/up` instead.
`restart` keeps the stored container config. `down/up` and `run-now` read both compose files when they run.

The container root is read-only. The `sommelier-data` named volume is at `/data`. The `./config` folder is at `/config`, read-only. Every write goes to `/data` or `/tmp`.

The image has a `HEALTHCHECK` that runs `scripts/container_health.py`. The check passes when nginx serves `index.html` and the mtime of the published `plan.json` is recent. That mtime is the time of the last complete run. `HEALTH_MAX_AGE_MINUTES` sets the age limit. It is optional, and the default is 600. `settings.py` owns this rule. The `run-now` service disables the check.

## LLM vs Data Boundary

- **CellarTracker is the system of record** for all wine metadata: Type/badge, Varietal, Appellation, scores, drinking windows, quantity.
- **Scripted rules** handle: plan generation (scheduling, priority, seasonal fit, evolution, holidays), inventory comparison, food-wine pairing suggestions.
- **Claude (LLM)** handles: tasting note generation and menu enrichment. Notes are contextual 1-2 sentence descriptions augmented with CellarTracker notes and food tags. Menu enrichment extracts structured food features (protein, preparation, richness, etc.) for pairing. If Claude CLI is unavailable, the pipeline continues with empty notes and falls back to keyword-based pairing.

## Plan Generation Rules

Implemented in `scripts/generate_plan.py`:
- Cadence: 1 bottle/week, 52 weeks, starting current Monday
- Priority: past peak → expiring → peak window → entering window → long-agers (held)
- Long-ager hold: BeginConsume > current_year + 2, or hold back 2 if 3+ bottles
- Seasonal: sparkling/rosé/white for spring-summer, bold reds for fall-winter
- Evolution tracks and holidays come from a YAML file at `PLAN_CONFIG` (default `/config/plan.yaml`; `bash fetch.sh` uses `config/plan.yaml`, gitignored, personal data). `scripts/plan_file.py` loads and checks it. A missing file gives the default holidays. A field that is not valid stops the run.
- Sample at `config/plan.yaml.sample`. `scripts/convert_plan_config.py` converts a v3 `plan_config.py` one time

## CellarTracker Data Sources

- `Table=Inventory` — bottles, varietals, types, scores (CT community + pro critics: WA, WS, BH, AG, JR, JS, JG), purchase data, valuation (system of record)
- `Table=Notes` — user's tasting notes (augments Claude-generated notes)
- `Table=FoodTags` — user's food pairing tags (augments pairing suggestions)
- `Table=Consumed` — consumption history (non-fatal; continues without if fetch fails)
- `rssnote.asp` RSS feed — community tasting notes for wines in your cellar (up to 200 items per fetch, cumulative cache at `$DATA_DIR/community_notes.json`). Needs `CT_COMMUNITY_NOTES_RSS` (the URL holds the `CK` token). Non-fatal; continues without if unavailable.

## Google Calendar Integration

- Menu data from Paprika-managed calendar via secret .ics URL
- `GOOGLE_CALENDAR_ICS_URL` in `.env`
- UTC timestamps converted to Pacific time (`America/Los_Angeles`)
- Keywords extracted using `wine_keywords.py`

## Wine-Food Pairing Engine ("The Sommelier")

- `wine_keywords.py`: single source of truth for keyword rules + enriched feature rules (protein, preparation, richness, spice, acidity, cuisine)
- `enrich_menu.py`: LLM extracts 13-field structured food features from menu text, cached by text hash
- Enriched-first fallback: try enriched features, fall back to keyword matching, fall back to neutral
- Three outcomes: Pairs Well (green), Sommelier Pick (blue), No Match (muted) — each with confidence level (high/medium/low)
- Suggestions prioritize urgent bottles in their drinking window
- Each meal gets a unique suggestion
- Varietal-aware: looks up inventory varietal for planned wines to improve matching

## Home Assistant Integration

The Home Assistant pull (REST sensors, `HA_ENABLED`) and its troubleshooting steps are in `docs/home-assistant.md`. Steps for the owner's own server are in the local `deploy` skill, which is not in git.

## Deployment Gotchas

- Shell scripts must have executable permission in git: `chmod +x *.sh && git add`
- Pre-commit hooks (ruff format) may modify files on first commit — re-stage and commit again
- Claude Code CLI is version-pinned in the Dockerfile — update the pin deliberately when upgrading

## Important Conventions

- Weeks begin on Monday
- Pairing scores: `"good"`, `"partial"`, `"poor"`, `"neutral"`
- `pipeline.sh` has all shared logic; `fetch.sh` and `fetch_docker.sh` are thin wrappers
- Plan is regenerated from scratch every pipeline run — no manual editing of `plan.json`
- Plan staged to `$DATA_DIR/plan.json`, only published to `$SITE_DIR` when complete with notes. `DATA_DIR` defaults to `/data`. `SITE_DIR` defaults to `$DATA_DIR/site`. `bash fetch.sh` sets them to the `data/` and `site/` folders of the repo. `docker-compose.yml` sets none of them. In the container the defaults `/data`, `/data/site` and `/config/plan.yaml` apply.
- `scripts/settings.py` is the one place for env rules: required variables, defaults, `DATA_DIR`, `SITE_DIR`, `PLAN_CONFIG` and the on/off rule of each feature. Do not read `os.environ` for settings in other scripts. Shell scripts use `settings.py check|enabled|get`. A feature turns on when all its settings are present and valid, and `<FEATURE>_ENABLED` is not set to false or to a value that is not valid. A feature with no settings (`home_assistant`) turns on only when its flag is a true value (`true`, `1` or `yes`). An `op://` value counts as not resolved.
- Shared utilities in `scripts/wine_utils.py` — do not duplicate `urgency_score`, `normalize`, `TYPE_TO_BADGE`, or `CURRENT_YEAR` in individual scripts
- Composite scoring in `scripts/scoring.py` — `composite_score()` combines: window position (50%), seasonal fit (25%), diversity (10%), CT quality (10%), community signals (5%). Lower score = schedule sooner.
- Community signals use RSS note data: recent score drift, note velocity, text-based window drift detection
- Plan labels show best available critic score: WA → WS → BH → AG → JR → JS → JG → CT
- `scoring.py` also owns `seasonal_score()` and red-subtype helpers (moved from `generate_plan.py` to avoid circular imports)

<!-- flow:begin rev 1 -->
## Developer workflow (flow)

Branches: `main` ← `milestone/vX.Y.Z` ← `issue/NNN-slug`. One PR for each
issue, to the milestone branch. A hotfix branches from `main` (the
`flow:hotfix` skill).

```text
/flow:plan-milestone → /flow:start-milestone
  per issue: /flow:start-issue → /flow:execute-plan → /commit → /flow:review-pr
             → /flow:commit-push-pr → /flow:address-pr-comments → /flow:finish-issue
/flow:finish-milestone → /flow:review-milestone → /flow:release-milestone
```

Hand-offs: start the next command through the Skill tool only when both
commands declare the same `model:`. Otherwise, tell the user to type it.
At an issue or milestone boundary, recommend `/clear` first.

Project facts for these commands are in `.claude/project.md`. The full
method is in the `flow:workflow` skill.
<!-- flow:end -->
