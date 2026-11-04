# Suggestions

Things that you may want to do. None is required.

[Back to the README](../README.md)

## Keep secrets in 1Password

Write `NAME=op://vault/item/field` lines in a file named `op.env`. The file holds references, not secrets. `op run` replaces each `op://` value in the environment of the command before the command starts.

```bash
op run --env-file=op.env -- docker compose up -d
```

- Compose reads the required variables in every command. Add the `op run` prefix to `logs`, `ps`, `down` and `run-now` also, for example `op run --env-file=op.env -- docker compose logs -f`. Without it, these commands stop with "required variable USERNAME is missing a value".
- This works with `compose.yaml`, which reads each `${NAME}` from the environment. It does not work with `docker-compose.yml`, which reads `env_file: .env`.
- For a run from a checkout, use `op run --env-file=op.env -- bash fetch.sh`. `op.env` must also hold the plain values, such as `SMTP_HOST`. With both required values resolved, `fetch.sh` does not read `.env`.
- A required value that still starts with `op://` stops the run with an error that names the variable. An optional value that still starts with `op://` turns its feature off and logs a warning.
