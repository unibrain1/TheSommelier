# CLAUDE.md

## Project Overview

Personal wine cellar management project. Maintains a multi-year wine drinking plan synchronized with a live CellarTracker inventory and a Google Calendar menu plan. A scheduled pipeline (every 30 minutes, 06:00–22:00 Pacific) generates a fresh plan, adds tasting notes via Claude, and publishes a web app.

The pipeline flow is in `README.md`. `plans/` and `data/` are gitignored.

## Running the Pipeline

```bash
bash fetch.sh
```

## Running Tests

Tests require `pytest` (install via `pip install pytest`).

## Docker Deployment

Production runs on **docker02**. Commands to restart, rebuild, or read logs are in the `deploy` skill (`.claude/skills/deploy/SKILL.md`).

Self-updating: container does `git pull` on startup and before each pipeline run.
Code changes pushed to GitHub are picked up automatically on next `down/up` or scheduled run.
`docker compose restart` does NOT re-run the entrypoint — use `down/up` instead.

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
- Evolution tracks and holidays configured in `scripts/plan_config.py` (gitignored, personal data)
- Sample at `scripts/plan_config.py.sample`

## CellarTracker Data Sources

- `Table=Inventory` — bottles, varietals, types, scores (CT community + pro critics: WA, WS, BH, AG, JR, JS, JG), purchase data, valuation (system of record)
- `Table=Notes` — user's tasting notes (augments Claude-generated notes)
- `Table=FoodTags` — user's food pairing tags (augments pairing suggestions)
- `Table=Consumed` — consumption history (non-fatal; continues without if fetch fails)
- `rssnote.asp` RSS feed — community tasting notes for wines in your cellar (up to 200 items per fetch, cumulative cache at `data/community_notes.json`). Requires `CK` token in 1Password. Non-fatal; continues without if unavailable.

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

The Home Assistant push and its troubleshooting steps are in the `deploy` skill (`.claude/skills/deploy/SKILL.md`).

## Deployment Gotchas

- Shell scripts must have executable permission in git: `chmod +x *.sh && git add`
- Docker volume mount requires `git config --global --add safe.directory /app` (handled in entrypoint)
- Pre-commit hooks (ruff format) may modify files on first commit — re-stage and commit again
- Claude Code CLI is version-pinned in the Dockerfile — update the pin deliberately when upgrading

## Important Conventions

- Weeks begin on Monday
- Pairing scores: `"good"`, `"partial"`, `"poor"`, `"neutral"`
- `pipeline.sh` has all shared logic; `fetch.sh` and `fetch_docker.sh` are thin wrappers
- Plan is regenerated from scratch every pipeline run — no manual editing of `plan.json`
- Plan staged to `data/plan.json`, only published to `site/` when complete with notes
- Shared utilities in `scripts/wine_utils.py` — do not duplicate `urgency_score`, `normalize`, `TYPE_TO_BADGE`, or `CURRENT_YEAR` in individual scripts
- Composite scoring in `scripts/scoring.py` — `composite_score()` combines: window position (50%), seasonal fit (25%), diversity (10%), CT quality (10%), community signals (5%). Lower score = schedule sooner.
- Community signals use RSS note data: recent score drift, note velocity, text-based window drift detection
- Plan labels show best available critic score: WA → WS → BH → AG → JR → JS → JG → CT
- `scoring.py` also owns `seasonal_score()` and red-subtype helpers (moved from `generate_plan.py` to avoid circular imports)
- Branch naming for issue work: `bug/<n>-<slug>` for bug-labeled issues, `feature/<n>-<slug>` for enhancements, `issue/<n>-<slug>` for all other issues. Issue PRs target the active milestone branch, not `main`.
