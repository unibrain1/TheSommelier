# Plan configuration

How to set your own holidays in `holidays.yaml` and your evolution tracks in `evolution_tracks.yaml`.

[Back to the README](../README.md)

Both files are optional. Each file works alone. Put them in the `config` folder. In Docker, the `config` folder is next to `compose.yaml`. For a run from a checkout, use the `config` folder of the repo. Git ignores both files.

To set your own, save `config/holidays.yaml.sample` as `config/holidays.yaml`, or `config/evolution_tracks.yaml.sample` as `config/evolution_tracks.yaml`. Then change the file for your cellar.

If a field is not valid, the sync stops with an error that names the file and the field, for example `/config/evolution_tracks.yaml: evolution_tracks[0].prefered_month: unknown field`. Each file allows only its own top-level key.

An old `plan.yaml` in the `config` folder is not used. The sync logs a warning that tells you to move its `holidays` to `holidays.yaml` and its `evolution_tracks` to `evolution_tracks.yaml`. Then delete `plan.yaml`.

## Holidays

Anchor special bottles to dates that matter to you. The plan generator finds the nearest week and assigns a high-score bottle.

Without `holidays.yaml`, the plan uses the five default holidays of the sample. An empty file, or `holidays:` with no value, also gives the default holidays. `holidays: []` gives no holidays.

```yaml
holidays:
  - name: Thanksgiving
    month: 11
    day: 26
  - name: Birthday   # your date
    month: 1
    day: 15
```

## Evolution tracks

Track how a specific wine develops across vintages by opening one bottle per year. The generator picks the most urgent vintage within the drinking window and schedules it in your preferred month.

Without `evolution_tracks.yaml`, the plan uses no evolution tracks.

```yaml
evolution_tracks:
  - label: Example Pinot Noir              # display name
    name_fragment: example pinot noir      # matched against inventory wine names
    season_months: [10, 11]                # fallback months when the preferred week is taken; start must not be after end
    preferred_month: 11                    # ideally November
```

If an evolution track falls on the same week as a holiday (for example, a November track near Thanksgiving), the occasions merge. The card shows both.
