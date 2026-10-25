---
name: deploy
description: "Use when you must restart, rebuild, or deploy the the-sommelier container on docker02, read pipeline logs there, or fix the Home Assistant push (thisweek.json, today.json)."
---

# Deploy on docker02

Production runs on docker02. Use SSH to restart or rebuild the application.

## Commands

```bash
# SSH to production host
ssh docker02

# Project directory
cd /home/ansible/container/the-sommelier

# First time or Dockerfile changes
docker compose up --build -d

# Code changes only (scripts, pipeline) — no rebuild needed
docker compose down && docker compose up -d

# One-shot test run
docker compose run --rm run-now

# View pipeline logs
docker logs the-sommelier --tail 100
```

## Container facts

- The container name is `the-sommelier`.
- It runs as non-root. The UID and GID are configurable in compose.
- It uses supercronic.
- Set the sync schedule with the `SYNC_SCHEDULE` env var. It accepts one or more cron expressions, separated by `;`. The default is `*/30 6-21 * * *;0 22 * * *` (every 30 minutes, 06:00 to 22:00 Pacific).
- The container runs `git pull` on startup and before each pipeline run.
- `docker compose restart` does NOT re-run the entrypoint. Use `down/up` instead.

## Home Assistant push

- The HA config is mounted locally at `/Volumes/config`.
- Each pipeline run pushes `thisweek.json` and `today.json` by SSH to `/config/www/` on HA.
- HA reads them with REST sensors (`scan_interval: 3600`). The sensors are in `/Volumes/config/configuration.yaml`.
- To make HA poll again at once, use **Developer Tools → Actions → `rest.reload`**.
- If the HA push fails, check the SSH key `~/.ssh/id_ed25519_ha` on docker02.
- Also check `authorized_keys` on HA.
