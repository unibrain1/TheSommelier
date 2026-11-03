# Plan configuration

How to set your own holidays and evolution tracks in `plan.yaml`.

[Back to the README](../README.md)

`plan.yaml` is optional. Without it, the plan uses the default holidays and no evolution tracks.

To set your own, save `config/plan.yaml.sample` as `config/plan.yaml` and change it for your cellar. In Docker, the `config` folder is next to `compose.yaml`. For a run from a checkout, use the `config` folder of the repo. Git ignores `config/plan.yaml`.

Without the file, the plan uses the five holidays of the sample and no evolution tracks. If a field is not valid, the sync stops with an error that names the field, for example `evolution_tracks[0].prefered_month: unknown field`.

## Holidays

Anchor special bottles to dates that matter to you. The plan generator finds the nearest week and assigns a high-score bottle. If the file has no `holidays` key, the plan uses the default holidays. `holidays: []` gives no holidays.

```yaml
holidays:
  - name: Thanksgiving
    month: 11
    day: 26
  - name: Anniversary   # your date
    month: 6
    day: 15
```

## Evolution tracks

Track how a specific wine develops across vintages by opening one bottle per year. The generator picks the most urgent vintage within the drinking window and schedules it in your preferred month.

```yaml
evolution_tracks:
  - label: Laurène              # display name
    name_fragment: laurène      # matched against inventory wine names
    season_months: [10, 11]     # fallback months when the preferred week is taken; start must not be after end
    preferred_month: 11         # ideally November
```

If an evolution track falls on the same week as a holiday (e.g., Laurène in November near Thanksgiving), the occasions merge — the card shows both.
