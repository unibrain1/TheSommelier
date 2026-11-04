# Configuration

How The Sommelier reads its settings, and a table of every environment variable.

[Back to the README](../README.md)

## How settings work

The Sommelier reads all settings from environment variables. In Docker, `compose.yaml` passes them to the container. For a run from a checkout, `.env.sample` lists the variables.

Three variables are required: `USERNAME`, `PASSWORD` and `GOOGLE_CALENDAR_ICS_URL`. If one is missing, empty, or starts with `op://`, the run stops with an error that names the variable.

Each optional feature has its own section in [Optional features](features.md). Each sync logs the state of each feature.

A feature turns on when all its settings are present and valid. To turn a feature off, remove its settings. One feature also has an on/off flag: `HA_ENABLED`. The flag accepts `true`, `false`, `1`, `0`, `yes` and `no`. `false`, `0` and `no` turn the feature off. An empty value or any other value also turns it off and logs a warning. Home Assistant has no other setting, so it turns on only when `HA_ENABLED` is `true`, `1` or `yes`.

Claude has no flag. Set one of its two keys, or neither. The [choice table](features.md#claude-tasting-notes-and-menu-enrichment) shows the three choices.

In `compose.yaml`, a secret is a line with `${NAME}`. Compose reads its value from `.env` or the host environment. Edit every other optional value in `compose.yaml`. To use an optional line, remove the `# ` from it.

Choose how the values reach The Sommelier:

- Plain values in `.env`: run `bash fetch.sh`. The script reads `.env` only when `USERNAME`, `PASSWORD` or `GOOGLE_CALENDAR_ICS_URL` is not in the shell. Then each value in `.env` replaces the same variable in the shell. When all three are in the shell (for example with `op run`), the script does not read `.env`.
- Docker with `compose.yaml`: compose reads `.env` or the host environment for each `${NAME}`.
- Docker with `docker-compose.yml`: compose passes the values with `env_file: .env`, so `.env` must hold plain values.
- 1Password references: see [Keep secrets in 1Password](suggestions.md#keep-secrets-in-1password).

Do not commit `.env`. Git ignores it.

### Environment variables

| Name | Default | Required | Feature and use |
| --- | --- | --- | --- |
| `USERNAME` | none | Yes | CellarTracker user name. |
| `PASSWORD` | none | Yes | CellarTracker password. |
| `GOOGLE_CALENDAR_ICS_URL` | none | Yes | Secret `.ics` URL of the menu calendar. |
| `TZ` | `America/Los_Angeles` in `compose.yaml` | No | Time zone of the plan weeks and the schedules. |
| `SYNC_SCHEDULE` | `*/30 6-21 * * *;0 22 * * *` | No | Cron expressions for the sync, separated by `;`. |
| `DIGEST_SEND_SCHEDULE` | `0 8 * * *` | No | Cron expression of the morning digest email. |
| `SMTP_USERNAME` | none | For the digest | Digest email. On when `SMTP_USERNAME`, `SMTP_PASSWORD` and `DIGEST_RECIPIENTS` are set. |
| `SMTP_PASSWORD` | none | For the digest | Digest email. SMTP password. |
| `DIGEST_RECIPIENTS` | none | For the digest | Digest email. Comma-separated addresses. |
| `SMTP_HOST` | `smtp.gmail.com` | No | Digest email. SMTP server. |
| `SMTP_PORT` | `587` | No | Digest email. SMTP port. |
| `SMTP_FROM` | value of `SMTP_USERNAME` | No | Digest email. Sender address. |
| `SITE_URL` | none | No | Public URL of your site. The digest email may use it to show the logo. |
| `CLAUDE_CODE_OAUTH_TOKEN` | none | For notes | Claude notes and menu enrichment with your Claude subscription. Set at most one Claude key. See [Claude login](features.md#claude-login). |
| `ANTHROPIC_API_KEY` | none | For notes | Claude notes and menu enrichment on your API bill. Set at most one Claude key. See [Claude login](features.md#claude-login). |
| `CT_COMMUNITY_NOTES_RSS` | none | For community notes | Community notes. RSS URL of the CellarTracker notes feed. It holds a token. |
| `HEALTHCHECK_URL` | none | For the ping | Healthchecks.io. The full ping URL of the check. It holds the key. On when the URL is set. |
| `HA_ENABLED` | off | No | Home Assistant. Only `true`, `1` or `yes` turns on the Home Assistant data. |
| `HEALTH_MAX_AGE_MINUTES` | `600` | No | Container health check. Docker marks the container as unhealthy when the plan is older than this many minutes. |
