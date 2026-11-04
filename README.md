# The Sommelier

<p align="center">
  <img src="docs/images/logo.png" alt="The Sommelier" width="350">
</p>

![The Sommelier](docs/images/screenshot.png)

The Sommelier reads your [CellarTracker](https://www.cellartracker.com) cellar and makes a 52-week drinking plan. It reads your meal plan from Google Calendar and suggests pairings from your cellar. Claude writes a tasting note for each bottle. A sync runs every 30 minutes during the day in a Docker container and publishes a web app. The plan follows the changes in your cellar.

## Features

- **Drinking plan** — One bottle a week for 52 weeks, ranked by drinking window, season, variety and CellarTracker rating. [How it works](docs/how-it-works.md)
- **Seasons and holidays** — Light wines for spring and summer, bold reds for fall and winter, special bottles on your holidays. [Plan configuration](docs/plan-configuration.md)
- **Food-wine pairing** — One suggestion from your cellar for each meal in your Google Calendar. [Pairing](docs/how-it-works.md#food-wine-pairing), [Menu Guide](docs/menu-guide.md)
- **Claude tasting notes** — A short note for each bottle. Use your Claude subscription or an API key. [Claude login](docs/features.md#claude-login)
- **Morning digest** — An email that names the wine to open with today's meal. [Digest email](docs/features.md#morning-digest-email)
- **Home Assistant** — REST sensors for the wine of the week and today's pairing. [Home Assistant](docs/home-assistant.md)
- **Healthchecks.io** — Each sync pings your check. [Healthchecks.io ping](docs/features.md#healthchecksio-ping)

## Quick Start

### Run with Docker

You need Docker with Compose, a [CellarTracker](https://www.cellartracker.com) account, and, for menu pairing (optional), the secret `.ics` URL of a Google Calendar with your meal plans (see [Menu plan setup](docs/menu-plan.md)). Without the calendar, the sync makes the plan but no pairings and no digest email. You do not need the repo.

1. Make a folder. Download the sample compose file:

   ```bash
   mkdir the-sommelier && cd the-sommelier
   curl -fsSLo compose.yaml https://raw.githubusercontent.com/unibrain1/TheSommelier/main/examples/compose.yaml
   ```

2. Optional: set your own holidays and evolution tracks. Download the plan sample, and change it for your cellar (see [Plan configuration](docs/plan-configuration.md)). Without `config/plan.yaml`, the plan uses the default holidays and no evolution tracks.

   ```bash
   mkdir config
   curl -fsSLo config/plan.yaml https://raw.githubusercontent.com/unibrain1/TheSommelier/main/config/plan.yaml.sample
   ```

3. Make a `.env` file next to `compose.yaml` for the two required variables and the optional calendar URL. Run `chmod 600 .env`.

   ```bash
   USERNAME=your_cellartracker_username
   PASSWORD=your_cellartracker_password
   # Optional: turns on menu pairing
   GOOGLE_CALENDAR_ICS_URL=https://calendar.google.com/calendar/ical/example/private-token/basic.ics
   ```

   A variable in the shell environment overrides the same variable in `.env`. Windows always sets `USERNAME`. There, set it in the shell before you start the container: `$env:USERNAME='your_cellartracker_username'; docker compose up -d`.

   To use an optional feature, remove the `# ` from its lines in `compose.yaml`. Put each secret (a line with `${NAME}`) in `.env`. Edit the other optional values in `compose.yaml`. See [Configuration](docs/configuration.md).
4. Start the container:

   ```bash
   docker compose up -d
   ```

5. Open `http://localhost:8080`. The first run takes longer when the Claude notes are on, because Claude writes a note for each bottle. Until the first run ends, the site shows no plan.

The site has no login. It serves your menu and `report.json`, which holds your cellar value. Port 8080 binds all host interfaces. To limit the site to the host, use `127.0.0.1:8080:8080` in `ports`.

To run a sync now, without waiting for the schedule:

```bash
docker compose run --rm run-now
```

### Update

A new release is a new image. The container does not update itself.

```bash
docker compose pull
docker compose down && docker compose up -d
```

## Documentation

| Page | Content |
| --- | --- |
| [Configuration](docs/configuration.md) | Flag and secret rules, and a table of every environment variable. |
| [Optional features](docs/features.md) | Schedules, digest email, Claude notes and login, community notes, Healthchecks.io, health check. |
| [Home Assistant](docs/home-assistant.md) | REST sensors for the wine of the week and today's pairing. |
| [Menu plan setup](docs/menu-plan.md) | Get your meal plan from Google Calendar. |
| [Menu Guide](docs/menu-guide.md) | Write menu entries for the best pairings. |
| [Plan configuration](docs/plan-configuration.md) | Holidays and evolution tracks in `plan.yaml`. |
| [The container](docs/the-container.md) | The sync, your files, and the health check. |
| [Upgrade from v3.x](docs/upgrade-from-v3.md) | Move from a v3.x checkout to the image. |
| [How it works](docs/how-it-works.md) | The sync steps, the ranking of wines, and the pairing. |
| [Run from a checkout](docs/running-from-a-checkout.md) | Develop, or build the image yourself. |
| [Suggestions](docs/suggestions.md) | Optional tips, such as 1Password. |
| [CLAUDE.md](CLAUDE.md) | Project conventions and deployment notes. |
