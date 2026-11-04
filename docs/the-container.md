# The container

What the container does, which files it keeps, and how to keep them in a host folder.

[Back to the README](../README.md)

The container runs a sync on a schedule. A sync fetches your CellarTracker cellar, builds the plan, and updates the site. If the menu feature is on, the sync also fetches your menu and adds wine pairings. See [Scheduling](features.md#scheduling) for the times.

Your files:

- `sommelier-data` is a Docker volume. It keeps the plan, the saved notes and the site. Keep it to keep your history.
- The `config` folder next to `compose.yaml` holds your optional `plan.yaml`.

To keep the data in a host folder instead of a volume, follow the comments in `compose.yaml`. Use the same folder for both services, and let user 1000 write to it.

## Image tags

The image is `ghcr.io/unibrain1/thesommelier`. It runs on amd64 and arm64.

| Tag | Content |
| --- | --- |
| `4.0.0` | One exact release. It never changes. |
| `4.0` | The latest patch release of 4.0. |
| `latest` | The latest release. |
| `edge` | The latest build of a push to the `main` branch or to a milestone branch, or of a manual run on a branch. It is not a release. |

To keep a fixed version, use `4.0.0`. To get fixes, use `4.0`. Do not use `edge` in production.

For the health check, see [Container health check](features.md#container-health-check).
