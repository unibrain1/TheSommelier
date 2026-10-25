# The Sommelier

<p align="center">
  <img src="docs/images/logo.png" alt="The Sommelier" width="350">
</p>

![The Sommelier](docs/images/screenshot.png)

Don't let good wine go to waste. The Sommelier is your personal wine advisor — it connects to your [CellarTracker](https://www.cellartracker.com) cellar and generates a 52-week drinking plan that prioritizes bottles past their peak or nearing the end of their drinking window. It fits wines to seasons, anchors special bottles to holidays, and reads your meal plan from Google Calendar to suggest specific pairings from your cellar. Claude adds contextual tasting notes for every bottle. The pipeline runs every 30 minutes during the day in a Docker container and publishes a self-updating web app — so your plan always reflects what's actually in your cellar.

## How It Works

```
┌─────────────────────────────────────────────────────────┐
│  SELF-UPDATE         git pull                           │
├─────────────────────────────────────────────────────────┤
│  FETCH (scripted)                                       │
│    CellarTracker inventory + notes + food tags           │
│    Google Calendar menu                                 │
├─────────────────────────────────────────────────────────┤
│  GENERATE PLAN (scripted — rules-based)                 │
│    52-week plan: priority, season, evolution, holidays   │
├─────────────────────────────────────────────────────────┤
│  GENERATE NOTES (LLM — Claude Code CLI)                 │
│    Contextual tasting notes, augmented with CT data      │
├─────────────────────────────────────────────────────────┤
│  COMPARE & PAIR (scripted)                              │
│    Inventory diff + food-wine pairing suggestions        │
├─────────────────────────────────────────────────────────┤
│  PUBLISH (atomic)                                       │
│    Complete plan with notes → site/                      │
└─────────────────────────────────────────────────────────┘
```

The plan regenerates from scratch every run. CellarTracker is the system of record for all wine metadata. Claude generates tasting notes. Everything else is deterministic.

Self-updating: push code to GitHub, next pipeline run picks it up automatically.

## How Wines Are Prioritized

The plan generator scores every bottle in your cellar and schedules the 52 most desirable wines — one per week. Each wine receives a **composite score** that blends four factors, each normalized to 0–100:

```
composite = 100 - (0.50 × window + 0.25 × season + 0.15 × diversity + 0.10 × quality)
```

Lower composite = schedule sooner. The four factors:

### Drinking window (50%)

The most important factor. A continuous score based on where the current year falls relative to a wine's Begin/EndConsume dates from CellarTracker.

- **Past peak** — Scores 75–100. These wines are fading — open them now while there's still a chance. The further past peak, the more urgent. A wine 6+ years past its window scores the maximum 100.
- **In window** — Scores 30–56. A bell curve centered on the midpoint of the drinking window, blended with end-of-window urgency. Wines near the sweet spot of their window score highest. Wines that just entered their window score lowest within this range.
- **Before window** — Scores 0–10. Not ready yet. Wines 3+ years from their window score 0.
- **No data** — Scores 35 (neutral). Treated as moderate urgency.

### Seasonal fit (25%)

Matches wine style to the time of year. Sparkling, rosé, and whites are preferred in spring and summer. Bold reds (Cabernet, Syrah, Barolo, Nebbiolo) are steered to fall and winter. Light reds like Pinot Noir and Gamay work year-round. A perfect seasonal match scores 100, a poor fit scores 0.

### Diversity (15%)

Prevents monotony. Scheduling the same wine, same producer, or same varietal too close together incurs a penalty that decays linearly over 5 weeks:

| Match | Penalty | Example |
|-------|---------|---------|
| Same wine | 60 | Two bottles of the same Barolo back-to-back |
| Same producer | 35 | Two Domaine Drouhin wines in 3 weeks |
| Same varietal | 20 | Cabernet in weeks 40 and 41 |

Only the strongest matching tier fires per comparison, but penalties accumulate across multiple nearby wines.

### CT quality score (10%)

CellarTracker's community rating, normalized so that CT 80 = 0 and CT 100 = 100. Higher-rated wines get a slight boost. Wines without a rating default to the community average (~88). This is the lightest weight — urgency and seasonal fit matter far more than ratings.

### Scheduling phases

The plan is built in three phases to ensure the most urgent bottles get placed first:

1. **Phase A (weeks 1–8)** — Only past-peak and expiring wines are eligible
2. **Phase B (weeks 9–16)** — Expiring-soon wines join the pool
3. **Phase C (weeks 17–52)** — All remaining wines compete by composite score

Evolution tracks and holiday anchors are pre-reserved before the main scheduling loop, so they always get their preferred weeks.

### Automatic hold-back rules

Some bottles are never scheduled, even if they score well:

- **Long-ager hold**: any bottle where `BeginConsume > current year + 2` is not scheduled
- **Quantity hold-back**: if you have 3+ bottles of the same wine, at most `quantity - 2` are scheduled (minimum 1)

## The Sommelier: Food-Wine Pairing

The pairing engine matches your meal plan against your cellar and suggests wines for upcoming meals. Suggestions prioritize bottles that need drinking soon. Each meal gets a unique suggestion, and the system is varietal-aware — it looks up inventory data for planned wines to improve matching accuracy.

Three outcomes per meal:
- **Pairs Well** (green) — A good match for the meal
- **Sommelier Pick** (blue) — An exceptional pairing from your cellar
- **No Match** (muted) — The meal doesn't pair well with available wines, or inventory is unavailable

For tips on writing meal plan entries that produce the best pairing suggestions, see [Menu Guide](docs/menu-guide.md).

## Quick Start

### Prerequisites

- Python 3.12+
- A [CellarTracker](https://www.cellartracker.com) account
- A Google Calendar with meal plans — see [Menu Plan Setup](#menu-plan-setup)
- [1Password CLI](https://developer.1password.com/docs/cli/) (`op`) — optional, see [Alternative Credential Methods](#alternative-credential-methods)
- [Paprika](https://www.paprikaapp.com) — optional, syncs meal plans to Google Calendar
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) (`claude`) — optional, for tasting notes

### Setup

```bash
pip install -r requirements.txt
cp .env.sample .env
cp scripts/plan_config.py.sample scripts/plan_config.py
# Edit .env with your credentials
# Edit scripts/plan_config.py with your holidays and evolution tracks
```

### Run

```bash
bash fetch.sh
# Serve the site:
python3 -m http.server -d site 8080
```

### Docker (Homelab)

```bash
# First time or Dockerfile changes
docker compose up --build -d

# Code changes only — no rebuild needed
docker compose down && docker compose up -d

# One-shot test run
docker compose run --rm run-now
```

Container: `the-sommelier`. Schedule configurable via `SYNC_SCHEDULE` env var (one or more cron expressions, separated by `;`). The default runs every 30 minutes from 6am to 10pm Pacific, so CellarTracker and Paprika changes show up within 30 minutes.
Runs as non-root (configure `user` in docker-compose.yml to match your host UID/GID).

`docker-compose.yml` is generic. It publishes no port and mounts no Home Assistant key. Put the settings for your host in `docker-compose.override.yml`, which git ignores. Docker Compose reads it automatically when you do not use `-f`. To start, copy the sample:

```bash
cp docker-compose.override.yml.sample docker-compose.override.yml
```

Then replace the placeholders: DNS servers, the reverse proxy host name, the published port and the SSH key path. Compose adds `dns` and `ports` to the lists in the base file, so set them in the override only.

## Home Assistant

Each pipeline run can push `thisweek.json` and `today.json` to Home Assistant. The pipeline copies each file by SSH (`sudo tee`) to `/config/www/` on the Home Assistant host.

The push runs only when both of these are true:

- `HA_SSH_TARGET` is set in `.env` (format: `user@host`).
- An SSH key is mounted into the container at `/run/secrets/ha_ssh_key`. Add this mount in `docker-compose.override.yml` (see the sample file).

If either one is missing, the log shows "Skipping Home Assistant push" and the pipeline continues.

Home Assistant reads the two files with REST sensors (`scan_interval: 3600`).

Troubleshooting:

- To make Home Assistant read the files now, go to Developer Tools → Actions and run `rest.reload`.
- If the push fails, make sure that the public half of the mounted key is in `authorized_keys` on the Home Assistant host.

## Menu Plan Setup

The pipeline reads your meal plan from a Google Calendar via its secret .ics URL. You can populate the calendar manually or use a recipe manager like [Paprika](https://www.paprikaapp.com).

### Using Paprika (recommended)

[Paprika](https://www.paprikaapp.com) is a recipe manager that can sync meal plans to Google Calendar.

1. In Paprika, go to **Settings → Calendar → Google Calendar**
2. Sign in with your Google account and select or create a calendar (e.g., "Menu Plan")
3. Plan your meals in Paprika's meal planner — they'll sync automatically to the calendar

### Using any calendar

You can also add meal events directly to any Google Calendar. Name each event after the meal (e.g., "Grilled Lamb Chops"). See [Menu Guide](docs/menu-guide.md) for tips on writing entries that produce the best pairing suggestions.

### Getting the calendar .ics URL

1. Open [Google Calendar](https://calendar.google.com) in a browser
2. Click the **⋮** menu next to your meal plan calendar → **Settings and sharing**
3. Scroll to **Integrate calendar**
4. Copy the **Secret address in iCal format** — it looks like:
   ```
   https://calendar.google.com/calendar/ical/...%40group.calendar.google.com/private-.../basic.ics
   ```
5. Add it to your `.env` as `GOOGLE_CALENDAR_ICS_URL`

## Plan Configuration

Edit `scripts/plan_config.py` to customize the plan for your cellar. A sample is provided at `scripts/plan_config.py.sample`.

### Holidays

Anchor special bottles to dates that matter to you. The plan generator finds the nearest week and assigns a high-score bottle.

```python
HOLIDAYS = [
    ("Thanksgiving", 11, 26),
    ("Christmas", 12, 18),
    ("New Year's Eve", 12, 28),
    ("Valentine's Day", 2, 14),
    ("4th of July", 7, 4),
    ("Anniversary", 6, 15),       # your date
    ("Birthday", 9, 22),          # your date
]
```

### Evolution Tracks

Track how a specific wine develops across vintages by opening one bottle per year. The generator picks the most urgent vintage within the drinking window and schedules it in your preferred month.

```python
EVOLUTION_TRACKS = [
    {
        "label": "Laurène",              # display name
        "name_fragment": "laurène",       # matched against inventory wine names
        "season_months": (10, 11),        # schedule in Oct–Nov
        "preferred_month": 11,            # ideally November
        "occasion_template": "{occasion} — Laurène evolution",
    },
]
```

If an evolution track falls on the same week as a holiday (e.g., Laurène in November near Thanksgiving), the occasions merge — the card shows both.

## Alternative Credential Methods

The default setup uses [1Password CLI](https://developer.1password.com/docs/cli/) to securely resolve CellarTracker credentials at runtime. If you don't use 1Password, you have several options.

### Option 1: Plain text in .env (simplest)

Replace the 1Password secret references in `.env` with your actual credentials:

```bash
# Instead of:
# USERNAME="op://vault/item/username"
# PASSWORD="op://vault/item/password"

# Use:
CT_USERNAME=your_cellartracker_username
CT_PASSWORD=your_cellartracker_password
```

Then modify `pipeline.sh` — replace the `op read` lines:

```bash
# Replace:
CT_USERNAME=$(op read "$USERNAME")
CT_PASSWORD=$(op read "$PASSWORD")

# With:
CT_USERNAME="${CT_USERNAME}"
CT_PASSWORD="${CT_PASSWORD}"
```

**Note:** Your credentials will be stored in plain text. Do not commit `.env` to git (it's already in `.gitignore`).

### Option 2: Environment variables

Set credentials as environment variables in your shell or Docker compose:

```yaml
# docker-compose.override.yml (git ignores this file)
services:
  the-sommelier:
    environment:
      - CT_USERNAME=your_cellartracker_username
      - CT_PASSWORD=your_cellartracker_password
  run-now:
    environment:
      - CT_USERNAME=your_cellartracker_username
      - CT_PASSWORD=your_cellartracker_password
```

The override then holds your credentials in plain text. Set its mode to 600 (`chmod 600 docker-compose.override.yml`).

And make the same `pipeline.sh` change as Option 1.

### Option 3: Other secret managers

Adapt the `op read` lines in `pipeline.sh` to use your preferred secret manager (Vault, AWS Secrets Manager, Bitwarden CLI, etc.).

## Documentation

- [CLAUDE.md](CLAUDE.md) — Project conventions, pipeline architecture, deployment gotchas
- [docs/menu-guide.md](docs/menu-guide.md) — How to write menu entries for best pairings
