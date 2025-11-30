# hit-modules

Shared plumbing for HIT microservices. This repo now contains:

- **Python middleware client** (`python/hit_modules`) for modules to fetch secrets/tokens via the provisioner.
- **Provisioner service** (FastAPI) that exposes `/healthz` and `/api/v1/secrets/database`.
- Reference tests that exercise the happy path.

## Running the provisioner locally

```
export PROVISIONER_ALLOW_ANONYMOUS=1
export HIT_PROVISIONER_DEFAULT_DB_URL="postgresql://local@localhost/dev"
uv run python -m hit_modules.provisioner
```

Then POST to `http://127.0.0.1:8700/api/v1/secrets/database` with:

```json
{
  "namespace": "shared",
  "secretKey": "auth-db"
}
```

The server will return a connection string (either from a JSON secrets file referenced by `PROVISIONER_SECRETS_PATH` or from `HIT_PROVISIONER_DEFAULT_DB_URL`).

## Tests

```
uv run --extra dev pytest
```

