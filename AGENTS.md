# AGENTS

## Project Summary

This repository contains `accessatlas-connector-netbox`, a small Python web
service that reads NetBox site data and exposes it in the Access Atlas site
feed format.

Current project expectations:

- framework: `FastAPI`
- dependency and environment management: `uv`
- production process: `gunicorn`
- linting: `ruff`
- testing: `pytest`
- container build support via `Dockerfile`

## Feed Contract

The service exposes a bearer-token protected JSON endpoint for Access Atlas.

NetBox site fields are mapped as follows:

- `id` -> `external_id`
- `facility` -> `code`
- `name` -> `name`
- `description` -> `description`
- `latitude` -> `latitude`
- `longitude` -> `longitude`

Rules:

- export all NetBox sites
- `code`, `description`, `latitude`, and `longitude` may be `null`
- pagination must be followed so the returned feed is complete

## Working Rules

- Keep commits conventional. Use Conventional Commits such as `feat: ...`,
  `fix: ...`, `chore: ...`, or `docs: ...`.
- Prefer small, clear changes with typed Python code and tests where practical.
- Preserve the Access Atlas contract unless the user explicitly asks for a
  schema change.

## Verification Rules

After modifying or writing Python code, verify the change with all of the
following where the environment allows:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run python -m compileall app tests
docker build -t accessatlas-connector-netbox .
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Notes:

- If a command cannot be run, report the exact reason clearly.
- Do not claim the app works unless tests, lint, and a build/start check have
  actually been attempted.
