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
- configured custom fields -> `tags`

All NetBox sites are exported. Missing `facility`, `description`, `latitude`, or `longitude` are returned as `null`.

Configured custom fields are exposed as Access Atlas tags. For NetBox choice fields, the connector uses the display label as the tag value. If the NetBox choice set defines a color, that color is passed through.

Tags are emitted as an array of label/color objects:

```json
"tags": [
  {"label": "Remote", "color": "orange"},
  {"label": "Annual programme", "color": "blue"}
]
```

Example:

```env
NETBOX_TAG_CUSTOM_FIELDS=site_type,access_class
```

## Configuration

Copy `.env.example` and set values:

```bash
cp .env.example .env
```

Required values:

- `NETBOX_URL`
- `NETBOX_TOKEN`
- `ACCESS_ATLAS_TOKEN`

Optional values:

- `NETBOX_SITE_FILTER`
- `NETBOX_TAG_CUSTOM_FIELDS`
- `DEBUG`

The NetBox API token must be able to read sites, custom fields, and custom field choice sets. In NetBox, this can be scoped by assigning the token to a user or group with the required object permissions.

### Site Filtering

By default, the connector exports all NetBox sites. To limit which sites are exported, set `NETBOX_SITE_FILTER` to a NetBox sites API query string without the leading `?`.

Examples:

```env
NETBOX_SITE_FILTER=status=active
NETBOX_SITE_FILTER=cf_storage_location=false
NETBOX_SITE_FILTER=status=active&cf_storage_location=false
```

You can build the filter in the NetBox sites GUI, confirm the displayed sites are correct, then copy everything after the `?` from the browser URL bar. For example, if NetBox shows:

```text
https://netbox.example.com/dcim/sites/?status=active&cf_storage_location=false
```

use:

```env
NETBOX_SITE_FILTER=status=active&cf_storage_location=false
```

## Local Development

```bash
uv sync --dev
uv run python -m app.run
```

The feed endpoint is:

```text
GET /site-feed.json
Authorization: Bearer <ACCESS_ATLAS_TOKEN>
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
