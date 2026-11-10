# Optional features

What each optional feature does, how to turn it on, and the keys that it uses.

[Back to the README](../README.md)

Each section lists what the feature does, how to turn it on, and its keys. The flag rules and the secret rule are in [Configuration](configuration.md). The [table](configuration.md#environment-variables) lists every key.

## Scheduling

The sync runs at the times in `SYNC_SCHEDULE`. The digest email has its own schedule. All cron times use `TZ`. The menu dates and the digest day always use Pacific time.

A scheduled sync that starts while another sync runs is skipped. The log shows `Skipping sync: another sync holds /tmp/sync.lock`, and the skipped run sends no ping.

To change a schedule, remove the `# ` from its line in `compose.yaml` and edit the value. All three keys are plain values.

| Key | Default |
| --- | --- |
| `SYNC_SCHEDULE` | `*/30 6-21 * * *;0 22 * * *` (one or more cron expressions, separated by `;`) |
| `DIGEST_SEND_SCHEDULE` | `0 8 * * *` |
| `TZ` | `America/Los_Angeles` in `compose.yaml` |

If you change `SYNC_SCHEDULE`, change the Healthchecks.io check and the Home Assistant `scan_interval` (see [Home Assistant](home-assistant.md)) to match.

## Menu pairing

The sync reads your meal plan from a Google Calendar and suggests a wine from your cellar for each meal. See [Menu plan setup](menu-plan.md).

It turns on when `GOOGLE_CALENDAR_ICS_URL` is set to a valid `http://` or `https://` URL. It has no flag. To turn it off, remove the setting. A `webcal://` URL does not work. Use the `https://` URL that Google gives. A space or a line break before or after the URL makes it not valid.

If the key is not set, or its value is not valid, the feature is off. The sync logs `menu: off` with the reason. If the value is not valid, the sync also logs a warning. The sync continues, and it publishes the plan as usual.

When the feature is off:

- The sync makes no pairings. The site shows 0 pairings.
- `today.json` has `"meal": null`.
- The sync removes the old `pairing_suggestions.json` from the site. If the site had one, the sync logs a warning.
- The morning digest has nothing to send.

When the feature is on, a calendar fetch that fails stops the run with an error.

| Key | Kind | Default |
| --- | --- | --- |
| `GOOGLE_CALENDAR_ICS_URL` | secret (the URL holds a token) | none |

## Morning digest email

The digest is a morning email that names the wine to open with today's meal. The email goes out at `DIGEST_SEND_SCHEDULE`, and only when the menu has an entry for today.

It turns on when `SMTP_USERNAME`, `SMTP_PASSWORD` and `DIGEST_RECIPIENTS` are all set. To stop the email, remove these settings.

The digest needs the menu feature. When the menu feature is off, the digest has nothing to send, so no digest email goes out.

| Key | Kind | Default |
| --- | --- | --- |
| `SMTP_USERNAME` | secret | none |
| `SMTP_PASSWORD` | secret | none |
| `DIGEST_RECIPIENTS` | plain, comma-separated addresses | none |
| `SMTP_HOST` | plain | `smtp.gmail.com` |
| `SMTP_PORT` | plain | `587` |
| `SMTP_FROM` | plain | value of `SMTP_USERNAME` |
| `SITE_URL` | plain, optional | none |

`SITE_URL` is the public URL of your site. The email embeds `site/logo.png` from the code in the image or the checkout, not from the run output. If that file is missing, the email uses `SITE_URL` + `/logo.png`. If `SITE_URL` is not set, or is not an `http://` or `https://` URL, the email has no logo, and the digest logs a warning.

## Claude tasting notes and menu enrichment

Claude writes a short tasting note for each bottle. It also reads the menu text to improve the pairings. A sync keeps a note while the wine and its marks (urgent, evolution and occasion) stay the same. A new score, window, CellarTracker note or community note does not change a kept note. Claude writes a note only for a new or changed week. Without Claude, a week keeps the note of the last plan if it has one. Other weeks have no note, and the pairings use keyword matching.

There are three choices. Set at most one key.

| Choice | Key to set | Keys to leave unset |
| --- | --- | --- |
| No Claude | none | `CLAUDE_CODE_OAUTH_TOKEN` and `ANTHROPIC_API_KEY` |
| Subscription | `CLAUDE_CODE_OAUTH_TOKEN` | `ANTHROPIC_API_KEY` |
| API | `ANTHROPIC_API_KEY` | `CLAUDE_CODE_OAUTH_TOKEN` |

If you set both keys, the feature is off. The sync writes no new notes and logs a warning that names both keys. Each sync logs the choice: `claude: on (subscription)`, `claude: on (API key)` or `claude: off` with the reason.

| Key | Kind | Default |
| --- | --- | --- |
| `CLAUDE_CODE_OAUTH_TOKEN` | secret | none |
| `ANTHROPIC_API_KEY` | secret | none |

### Claude login

For the subscription, the container logs in with a token. The runs use your Claude subscription.

1. On a computer where you are logged in to Claude Code, run `claude setup-token`.
2. Put the token in `.env` as `CLAUDE_CODE_OAUTH_TOKEN`. Remove the `# ` from its line in `compose.yaml`.
3. Run `docker compose down && docker compose up -d`.

The token expires one year after you make it. After that, the sync continues with no new notes. Make a new token and replace the value.

For the API, put your Anthropic API key in `.env` as `ANTHROPIC_API_KEY`. Remove the `# ` from its line in `compose.yaml`. Then run `docker compose down && docker compose up -d`. The runs go on your API bill.

## CellarTracker community notes

The sync reads the CellarTracker RSS feed of community notes for the wines in your cellar. The plan uses the notes for score drift, note velocity and drinking-window drift. If the feed is not available, the sync continues without it.

It turns on when `CT_COMMUNITY_NOTES_RSS` is set. To turn it off, remove the setting.

| Key | Kind | Default |
| --- | --- | --- |
| `CT_COMMUNITY_NOTES_RSS` | secret (the URL holds a token) | none |

## Healthchecks.io ping

Each sync pings Healthchecks.io at the end. A successful run pings `HEALTHCHECK_URL`. A failed run pings the same URL with `/fail` added.

Copy the ping URL from the page of the check on Healthchecks.io. Each form of the URL works: the UUID form, the ping key and slug form, and a self-hosted server. The URL must start with `https://` or `http://`. It must have no query (`?`), no fragment (`#`), no user name (`@`), no backslash, no space, and no control or non-ASCII character. If the URL is not valid, the ping is off and the log names `HEALTHCHECK_URL`.

It turns on when `HEALTHCHECK_URL` is set. To turn it off, remove the setting.

| Key | Kind | Default |
| --- | --- | --- |
| `HEALTHCHECK_URL` | secret (the URL holds the key) | none |

The check schedule on Healthchecks.io must match `SYNC_SCHEDULE`. If it does not match, the check reports "down" when no run is due. For the default schedule, use these settings:

- Schedule type: Cron
- Expression: `*/30 6-21 * * *`
- Time zone: `America/Los_Angeles`
- Grace time: 45 minutes

Healthchecks.io accepts only one cron expression. The 22:00 run sends one extra ping, which has no effect. If you change `SYNC_SCHEDULE`, change the check schedule in the same change.

The success ping body starts with `Pipeline completed successfully.` It has the `Bottles`, `Weeks planned` and `Time` lines. It has a line `Warnings: N`, where N is the number of warnings in the run.

The sync counts a warning when an optional step fails. Examples are community notes, tasting notes, menu enrichment, the Home Assistant JSON files and digest generation. The sync also counts each `WARNING:` line of the settings check. An example is a feature that is off because some of its settings are missing or not valid. A feature with no settings at all adds no warning.

If N is greater than 0, the ping lists each warning line below the count. The warning lines hold no secret values.

When some weeks get no tasting note, the log names those weeks, and the run counts a warning. When no note is made, the note step fails, the run counts a warning, and the sync publishes the plan with no new notes.

When Claude enriches some menu entries but not all, the run counts the warning "Menu enrichment incomplete". When Claude enriches no entry that needs it, or the enrichment files cannot be written, the run counts the warning "Menu enrichment failed". In both cases, pairing uses keyword matching for each meal that has no enrichment. A menu with all entries in the cache needs no Claude call and adds no warning.

A failed run sends the body `Pipeline failed at step: <step>. Check container logs for details.` The step name is one of a fixed set, such as `settings`, `fetch`, `parse`, `plan`, `compare`, `pairing` or `publish`. The body holds no secret.

The scheduled digest send (`DIGEST_SEND_SCHEDULE`) also pings `HEALTHCHECK_URL`. It sends a fail ping with the cause when the send fails. It also sends a fail ping when `digest.json` is stale. The file is stale when it is not for today. Then the last sync did not finish, or it did not make a new digest. Healthchecks.io then sends its alert. The next successful sync sends a success ping, and the check returns to up.

A manual run with `--dry-run` or `--force` sends no ping. So does a digest run that reports "Nothing to send", "already sent" or "not configured".

## Container health check

Docker marks the container as unhealthy when the site stops responding or when the plan is old. `HEALTH_MAX_AGE_MINUTES` sets how many minutes old the plan may be. It is a plain value. The default is `600`.
