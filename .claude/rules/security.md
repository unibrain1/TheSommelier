---
paths: ["*.sh", "scripts/**", "site/**", "Dockerfile", "docker-compose*.yml", "nginx.conf"]
---

# Security rules

These rules are for The Sommelier only. The `flow:security-reviewer` agent
and `/flow:security-review` read them in addition to their general checks.

## Attack surface

The project runs on a schedule in a Docker container. It reads data from
external services and publishes a static web page.

| Area | Files | Risk |
| --- | --- | --- |
| Shell | `pipeline.sh`, `fetch.sh`, `fetch_docker.sh`, `entrypoint.sh`, `send_digest.sh` | Secrets, quoting, error handling |
| External data | `curl` calls in `pipeline.sh`, `scripts/fetch_community_notes.py` | Untrusted input from CellarTracker, Google Calendar and RSS |
| Settings and secrets | `scripts/settings.py`, `scripts/healthcheck.py` | Secrets in logs and errors, the Healthchecks key in a URL |
| LLM calls | `scripts/wine_utils.py` (`claude` CLI), `scripts/generate_notes.py`, `scripts/enrich_menu.py` | Prompt injection from menu text and wine notes |
| Site | `site/index.html`, `site/picklist.html` | XSS from JSON data |
| Container | `Dockerfile`, `docker-compose.yml` | User, mounts, pinned versions |

## Secrets

- A secret comes from the environment that the host injects (for example
  `op run`, an Ansible vault, or compose `env_file`) or from a git-ignored
  `.env` file. It must never be in source code, a sample file, a log
  message or an error message.
- A secret in a command argument or a URL is a finding. Another process can
  read it with `ps`, and `curl` can print it in an error. The CellarTracker
  query URL in `pipeline.sh` is a known case.
- `set -x`, or an `echo` of a variable that holds a secret, is a finding.

## Shell

- Each script starts with `set -euo pipefail`. Each wrapper script sources
  `pipeline.sh` correctly.
- A variable expansion that comes from external data or from the
  environment must be quoted.
- No `eval`, and no command that a script builds from a string.
- A temporary file uses `mktemp`, not a fixed or predictable name.
- A script stops when a required setting is missing.

## Python

- `subprocess` gets an argument list, never `shell=True`.
- A file path built from external data (wine names, menu text, RSS fields)
  needs a check.
- No `pickle`, `eval` or `exec` on external data. YAML uses `SafeLoader`.
- Each HTTP request has a timeout and keeps TLS verification on.
- The RSS parser must not resolve external XML entities.

## Prompt injection

- Menu text, CellarTracker notes and RSS notes are untrusted. A prompt marks
  them as data, not as instructions.
- The code checks the LLM output (type, length, expected fields) before it
  writes the output to JSON.
- The `claude` CLI call gets no tools or permissions that let injected text
  run commands or change files.

## Site

- JS writes JSON data into the page only through `escapeHtml()` or
  `textContent`. Wine names, notes, appellations and menu text are external
  data.
- A URL built from JSON data needs a scheme check (`javascript:`).
- A script or stylesheet from a third-party host has a pinned version.

## Container

- The container does not run as root without a reason.
- A mount gives the container only the host access that it needs.
- Each base image and tool has a pinned version.
