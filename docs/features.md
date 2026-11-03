# Optional features

What each optional feature does, how to turn it on, and the keys that it uses.

[Back to the README](../README.md)

Each section lists what the feature does, how to turn it on, and its keys. The flag rules and the secret rule are in [Configuration](configuration.md). The [table](configuration.md#environment-variables) lists every key.

## Scheduling

The sync runs at the times in `SYNC_SCHEDULE`. The digest email has its own schedule. All cron times use `TZ`. The menu dates and the digest day always use Pacific time.

To change a schedule, remove the `# ` from its line in `compose.yaml` and edit the value. All three keys are plain values.

| Key | Default |
| --- | --- |
| `SYNC_SCHEDULE` | `*/30 6-21 * * *;0 22 * * *` (one or more cron expressions, separated by `;`) |
| `DIGEST_SEND_SCHEDULE` | `0 8 * * *` |
| `TZ` | `America/Los_Angeles` in `compose.yaml` |

If you change `SYNC_SCHEDULE`, change the Healthchecks.io check and the Home Assistant `scan_interval` (see [Home Assistant](home-assistant.md)) to match.

## Morning digest email

The digest is a morning email that names the wine to open with today's meal. The email goes out at `DIGEST_SEND_SCHEDULE`, and only when the menu has an entry for today.

It turns on when `SMTP_USERNAME`, `SMTP_PASSWORD` and `DIGEST_RECIPIENTS` are all set. `DIGEST_ENABLED=false` stops the email.

| Key | Kind | Default |
| --- | --- | --- |
| `SMTP_USERNAME` | secret | none |
| `SMTP_PASSWORD` | secret | none |
| `DIGEST_RECIPIENTS` | plain, comma-separated addresses | none |
| `SMTP_HOST` | plain | `smtp.gmail.com` |
| `SMTP_PORT` | plain | `587` |
| `SMTP_FROM` | plain | value of `SMTP_USERNAME` |
| `DIGEST_ENABLED` | flag | on when the settings are set |
| `SITE_URL` | plain, optional | none |

`SITE_URL` is the public URL of your site. The email may use it to show the logo.

## Claude tasting notes and menu enrichment

Claude writes a short tasting note for each bottle. It also reads the menu text to improve the pairings. Without Claude, the notes are empty and the pairings use keyword matching.

It turns on when `CLAUDE_CODE_OAUTH_TOKEN` is set. See [Claude login](#claude-login) to make the token.

| Key | Kind | Default |
| --- | --- | --- |
| `CLAUDE_CODE_OAUTH_TOKEN` | secret | none |
| `CLAUDE_ENABLED` | flag | on when the token is set |

### Claude login

The container logs in with a token. The token uses your Claude subscription.

1. On a computer where you are logged in to Claude Code, run `claude setup-token`.
2. Put the token in `.env` as `CLAUDE_CODE_OAUTH_TOKEN`. Remove the `# ` from its line in `compose.yaml`.
3. Run `docker compose down && docker compose up -d`.

Do not set `ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN`. The `claude` CLI uses them before the token, so the runs go on the API bill and not on your subscription.

The token expires one year after you make it. After that, the sync continues with empty notes. Make a new token and replace the value.

## CellarTracker community notes

The sync reads the CellarTracker RSS feed of community notes for the wines in your cellar. The plan uses the notes for score drift, note velocity and drinking-window drift. If the feed is not available, the sync continues without it.

It turns on when `CT_COMMUNITY_NOTES_RSS` is set.

| Key | Kind | Default |
| --- | --- | --- |
| `CT_COMMUNITY_NOTES_RSS` | secret (the URL holds a token) | none |
| `COMMUNITY_NOTES_ENABLED` | flag | on when the URL is set |

## Healthchecks.io ping

Each sync pings Healthchecks.io at the end. A successful run pings the check. A failed run pings `/fail`. The ping goes to `<HEALTHCHECK_BASE_URL>/<HEALTHCHECK_PING_KEY>/<HEALTHCHECK_SLUG>`.

It turns on when `HEALTHCHECK_PING_KEY` is set.

| Key | Kind | Default |
| --- | --- | --- |
| `HEALTHCHECK_PING_KEY` | secret | none |
| `HEALTHCHECK_SLUG` | plain | `the-sommelier` |
| `HEALTHCHECK_BASE_URL` | plain | `https://hc-ping.com` |
| `HEALTHCHECK_ENABLED` | flag | on when the key is set |

The check schedule on Healthchecks.io must match `SYNC_SCHEDULE`. If it does not match, the check reports "down" when no run is due. For the default schedule, use these settings:

- Schedule type: Cron
- Expression: `*/30 6-21 * * *`
- Time zone: `America/Los_Angeles`
- Grace time: 45 minutes

Healthchecks.io accepts only one cron expression. The 22:00 run sends one extra ping, which has no effect. If you change `SYNC_SCHEDULE`, change the check schedule in the same change.

## Container health check

Docker marks the container as unhealthy when the site stops responding or when the plan is old. `HEALTH_MAX_AGE_MINUTES` sets how many minutes old the plan may be. It is a plain value. The default is `600`.
