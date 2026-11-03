# Home Assistant

How to read the plan and the meal in Home Assistant with REST sensors.

[Back to the README](../README.md)

Home Assistant pulls `thisweek.json` and `today.json` from the site with REST sensors. The container needs no Home Assistant credentials.

The feature is off by default. It has one key.

| Key | Kind | Default |
| --- | --- | --- |
| `HA_ENABLED` | flag | off |

The flag is a plain value. [Configuration](configuration.md) explains the flag rules. To turn it on, set `HA_ENABLED=true` in the environment of the container (in `compose.yaml`, remove the `# ` from its line). When the feature is off, both URLs give 404. `HA_ENABLED=false` and a value that is not valid also keep the feature off. The `Settings:` lines in `docker compose logs the-sommelier` show the state.

The site serves the files at these URLs:

- `https://<site host>/thisweek.json`: the wine for the current week.
- `https://<site host>/today.json`: the meal and the pairing for today. The `meal` field is `null` when no meal is planned.

Add this to `configuration.yaml` in Home Assistant. Replace `<site host>` with the host name of your site. Without a reverse proxy, the site is `http://<docker host>:8080`.

```yaml
rest:
  - resource: https://<site host>/thisweek.json
    scan_interval: 1800
    sensor:
      - name: Sommelier this week
        value_template: "{{ value_json.name }}"
        json_attributes:
          - vintage
          - badge
          - date
          - note
          - location
          - window
  - resource: https://<site host>/today.json
    scan_interval: 1800
    sensor:
      - name: Sommelier today
        value_template: "{{ value_json.meal | default('No meal planned', true) }}"
        json_attributes:
          - date
          - planned_wine
          - planned_vintage
          - suggested_bottle
          - pairing
```

With the default `SYNC_SCHEDULE` (every 30 minutes, 06:00–22:00 Pacific with the default `TZ`), the files change at most each 1800 seconds, so a `scan_interval` below 1800 gives no new data. If you change `SYNC_SCHEDULE`, change `scan_interval` to match. Use `http://` only on a trusted local network.

## Troubleshooting

- To make Home Assistant read the files now, go to Developer Tools → Actions and run `rest.reload`.
- If a URL gives 404, check that `HA_ENABLED=true` is in the container environment and that the last sync did not skip the files.
- If a sensor shows `unavailable` and the log has "WARNING: thisweek.py failed" or "WARNING: today.py failed", the sync failed to make the file and removed it. Read the log to find the cause, then run the sync again.
- If a sensor shows `unknown`, open the URL in a browser and compare the field names with `value_template` and `json_attributes`.
