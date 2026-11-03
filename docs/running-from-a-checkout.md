# Run from a checkout

How to run The Sommelier from a repo checkout, or build the image yourself.

[Back to the README](../README.md)

Use a checkout to develop, or to build the image yourself. To run the published image, use [Run with Docker](../README.md#run-with-docker).

## Prerequisites

- Python 3.12+
- A [CellarTracker](https://www.cellartracker.com) account
- A Google Calendar with meal plans — see [Menu plan setup](menu-plan.md)
- [1Password CLI](https://developer.1password.com/docs/cli/) (`op`) — optional, only to run `op run` with secret references, see [Keep secrets in 1Password](suggestions.md#keep-secrets-in-1password)
- [Paprika](https://www.paprikaapp.com) — optional, syncs meal plans to Google Calendar
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) (`claude`) — optional, for tasting notes. Set `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY`, see [Claude login](features.md#claude-login)

## Set up and run

```bash
pip install -r requirements.txt
cp .env.sample .env
cp config/plan.yaml.sample config/plan.yaml
# Edit .env with your values (see docs/configuration.md)
# Edit config/plan.yaml with your holidays and evolution tracks
```


```bash
bash fetch.sh
# Serve the site:
python3 -m http.server -d site 8080
```

## Build the image

Build and run the image from the checkout. `docker-compose.yml` has `build: .` and `env_file: .env`:

```bash
docker compose build
docker compose down && docker compose up -d
docker compose run --rm run-now
```

## Settings for your host

`docker-compose.yml` is generic. It publishes no port. Put the settings for your host in `docker-compose.override.yml`, which git ignores. To start, copy the sample:

```bash
cp docker-compose.override.yml.sample docker-compose.override.yml
```

Then replace the placeholders: DNS servers, the reverse proxy host name, and the published port. Compose adds `dns` and `ports` to the lists in the base file, so set them in the override only.
