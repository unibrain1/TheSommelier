# Upgrade from v3.x

How to move from a v3.x checkout to the v4.0.0 image.

[Back to the README](../README.md)

Before v4.0.0 the container ran from a repo checkout on the host. It updated itself from git, read its secrets from 1Password with the `op` CLI, and pushed two files to Home Assistant over SSH. v4.0.0 runs from a published image.

1. Make the folder for the new setup. Download `compose.yaml` as in [Run with Docker](../README.md#run-with-docker). Do not download `holidays.yaml` and `evolution_tracks.yaml`: step 3 makes the files that your `plan_config.py` needs.
2. **Settings.** Copy the old `.env` next to `compose.yaml`. Replace each `op://` value with a plain value, or use [1Password](suggestions.md#keep-secrets-in-1password). The container does not call `op`. Remove `OP_SERVICE_ACCOUNT_TOKEN` from `.env`, because the container does not use it. For each optional variable in the old `.env`, remove the `# ` from its line in `compose.yaml`. Move each plain value from `.env` into `compose.yaml`. Plain values are, for example, `DIGEST_RECIPIENTS`, `SMTP_HOST` and `SYNC_SCHEDULE`. [Configuration](configuration.md) tells which values are secrets. Replace `HEALTHCHECK_PING_KEY` with `HEALTHCHECK_URL`, the full ping URL of the check. v3 used the check slug `the-sommelier`.
3. **Plan settings.** Before v4.0.0 the settings were in `scripts/plan_config.py`. Convert that file one time with the converter in the image. Make the `config` folder, and let user 1000 write to it:

   ```bash
   mkdir -p config && chmod 777 config
   docker run --rm --entrypoint python3 -u 1000:1000 \
     -v /path/to/old/checkout/scripts/plan_config.py:/in/plan_config.py:ro \
     -v "$PWD/config":/out ghcr.io/unibrain1/thesommelier:4.1 \
     /app/scripts/convert_plan_config.py /in/plan_config.py /out
   ```

   The converter writes `holidays.yaml` and `evolution_tracks.yaml` in `/out`, one file for each setting that `plan_config.py` has. It does not write over a file that exists. Run `chmod 755 config` after.
4. **Data folder.** Stop the old container in the old checkout. Copy the files of the old `data/` folder and the `*.json` files of the old `site/` folder into the `/data` volume. This keeps the plan history, the caches and the digest state. Put the old `data/` files in a folder named `old-data`, and the old `site/*.json` files in `old-data/site`. Then copy them with a temporary container:

   ```bash
   (cd /path/to/old/checkout && docker compose down)
   docker run --rm -v the-sommelier_sommelier-data:/data -v "$PWD/old-data":/old:ro \
     alpine:3.20 sh -c 'cp -a /old/. /data/ && chown -R 1000:1000 /data'
   ```

   `compose.yaml` sets the project name `the-sommelier`, so the volume is always `the-sommelier_sommelier-data`. The first `docker compose up` warns that the volume was not made by Compose. This is expected.
5. **Claude.** Make a token with `claude setup-token`, and set `CLAUDE_CODE_OAUTH_TOKEN`. To use the API bill instead, set `ANTHROPIC_API_KEY`. Set one key only. See [Claude login](features.md#claude-login).
6. **Home Assistant.** The SSH push is gone. Set `HA_ENABLED: "true"` in `compose.yaml`, then change the `resource` of each REST sensor from the local URL of the `/config/www` files to the two site URLs in [Home Assistant](home-assistant.md). Then do these steps:
   1. Go to Developer Tools → Actions and run `rest.reload`.
   2. Remove `HA_SSH_TARGET` from `.env`. Remove the `ha_ssh_key` mount from `docker-compose.override.yml`.
   3. Delete `thisweek.json` and `today.json` from `/config/www` on the Home Assistant host.
   4. On the Home Assistant host, remove the public half of the push key from `authorized_keys`, and remove the `sudo` rule that let that user run `tee`. On the Docker host, delete the private key file.
7. **Start.** Start the container with `docker compose up -d`. Check the `Settings:` lines in `docker compose logs the-sommelier`. Each feature that you set up must show `on`. Later, update the image with `docker compose pull`, then `docker compose down && docker compose up -d`. The host needs no git checkout now.
8. Check one full run: the logs, the site, the digest and the Home Assistant sensors. Then remove the old checkout.
