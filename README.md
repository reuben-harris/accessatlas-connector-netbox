# accessatlas-connector-netbox

**accessatlas-connector-netbox** is a small Python web service that reads NetBox sites and exposes them in the Access Atlas site feed format.

## Mapping

NetBox site fields map to Access Atlas as follows:

- `id` -> `external_id`
- `facility` -> `code`
- `name` -> `name`
- `description` -> `description`
- `latitude` -> `latitude`
- `longitude` -> `longitude`

All NetBox sites are exported. Missing `facility`, `description`, `latitude`,
or `longitude` are returned as `null`.

## Configuration

Copy `.env.example` and set values:

```bash
cp .env.example .env
```

All values are required to be set for both production and development.

## Local Development

```bash
uv sync --dev
uv run python -m app.run
```

The feed endpoint is:

```text
GET /site-feed.json
Authorization: Bearer <ACCESS_ATLAS_BEARER_TOKEN>
```

Health endpoint:

```text
GET /healthz
```

## Tests

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

## Container

Build locally:

```bash
uv lock
docker build -t accessatlas-connector-netbox .
```

Run:

```bash
docker run --rm -p 8000:8000 --env-file .env accessatlas-connector-netbox
```
