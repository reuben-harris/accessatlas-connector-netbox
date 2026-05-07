from datetime import UTC, datetime

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings, get_settings
from app.main import app, get_netbox_client, get_site_feed, verify_bearer_token
from app.models import AccessAtlasFeed
from app.netbox import (
    NetBoxClient,
    NetBoxUpstreamConnectionError,
    NetBoxUpstreamHTTPError,
    NetBoxUpstreamPayloadError,
)


class DummySettings:
    netbox_url = "https://netbox.example.com"
    netbox_token = "netbox-token"
    access_atlas_token = "atlas-token"
    netbox_site_filter = ""
    tag_custom_fields: list[str] = []


class TagSettings(DummySettings):
    tag_custom_fields = ["test", "plain", "missing"]


class SingleTagFieldSettings(DummySettings):
    tag_custom_fields = ["test"]


class FilterSettings(DummySettings):
    netbox_site_filter = "status=active&cf_storage_location=false"


class EncodedFilterSettings(DummySettings):
    netbox_site_filter = "status=active&q=Main%20Site"


def test_settings_parse_tag_fields_debug_and_ignore_unrelated_values():
    settings = Settings(
        NETBOX_URL="https://netbox.example.com",
        NETBOX_TOKEN="netbox-token",
        ACCESS_ATLAS_TOKEN="atlas-token",
        NETBOX_SITE_FILTER="status=active",
        NETBOX_TAG_CUSTOM_FIELDS="test, plain, ,",
        DEBUG="true",
        GITHUB_TOKEN="ignored",
    )

    assert settings.netbox_site_filter == "status=active"
    assert settings.tag_custom_fields == ["test", "plain"]
    assert settings.debug is True


def test_settings_reject_site_filter_with_leading_question_mark():
    with pytest.raises(ValidationError, match="without a leading"):
        Settings(
            NETBOX_URL="https://netbox.example.com",
            NETBOX_TOKEN="netbox-token",
            ACCESS_ATLAS_TOKEN="atlas-token",
            NETBOX_SITE_FILTER="?status=active",
        )


@pytest.mark.parametrize(
    "site_filter",
    [
        "https://netbox.example.com/api/dcim/sites/?status=active",
        "/api/dcim/sites/?status=active",
    ],
)
def test_settings_reject_site_filter_full_urls_and_paths(site_filter: str):
    with pytest.raises(ValidationError, match="query string parameters"):
        Settings(
            NETBOX_URL="https://netbox.example.com",
            NETBOX_TOKEN="netbox-token",
            ACCESS_ATLAS_TOKEN="atlas-token",
            NETBOX_SITE_FILTER=site_filter,
        )


def test_site_feed_endpoint_requires_bearer_token_and_serializes_feed():
    class StubNetBoxClient:
        async def fetch_feed(self):
            return AccessAtlasFeed(
                schema_version="1.0",
                source_name="netbox",
                generated_at=datetime(2026, 5, 7, 10, 11, 12, tzinfo=UTC),
                sites=[
                    {
                        "external_id": "1",
                        "code": "AKL-01",
                        "name": "Auckland",
                        "description": "Primary site",
                        "latitude": -36.8485,
                        "longitude": 174.7633,
                        "tags": [{"label": "Remote", "color": "orange"}],
                    }
                ],
            )

    app.dependency_overrides[get_netbox_client] = lambda: StubNetBoxClient()
    app.dependency_overrides[get_settings] = lambda: DummySettings()
    try:
        with TestClient(app) as test_client:
            unauthorized_response = test_client.get("/site-feed.json")
            authorized_response = test_client.get(
                "/site-feed.json",
                headers={"Authorization": "Bearer atlas-token"},
            )
    finally:
        app.dependency_overrides.clear()

    assert unauthorized_response.status_code == 401
    assert authorized_response.status_code == 200
    assert authorized_response.json() == {
        "schema_version": "1.0",
        "source_name": "netbox",
        "generated_at": "2026-05-07T10:11:12Z",
        "sites": [
            {
                "external_id": "1",
                "code": "AKL-01",
                "name": "Auckland",
                "description": "Primary site",
                "latitude": -36.8485,
                "longitude": 174.7633,
                "tags": [{"label": "Remote", "color": "orange"}],
            }
        ],
    }


@pytest.mark.parametrize(
    ("upstream_error", "expected_status_code", "expected_detail"),
    [
        (
            NetBoxUpstreamHTTPError(
                httpx.Response(
                    403,
                    request=httpx.Request(
                        "GET",
                        "https://netbox.example.com/api/dcim/sites/?status=active",
                    ),
                )
            ),
            502,
            "Upstream NetBox request failed",
        ),
        (
            NetBoxUpstreamConnectionError("Failed to connect to NetBox"),
            503,
            "NetBox is unavailable",
        ),
        (
            NetBoxUpstreamPayloadError("NetBox returned an invalid payload"),
            502,
            "Upstream NetBox returned an invalid payload",
        ),
    ],
)
def test_site_feed_endpoint_translates_upstream_errors(
    upstream_error: Exception,
    expected_status_code: int,
    expected_detail: str,
):
    class FailingNetBoxClient:
        async def fetch_feed(self):
            raise upstream_error

    app.dependency_overrides[get_netbox_client] = lambda: FailingNetBoxClient()
    app.dependency_overrides[get_settings] = lambda: DummySettings()
    try:
        with TestClient(app) as test_client:
            response = test_client.get(
                "/site-feed.json",
                headers={"Authorization": "Bearer atlas-token"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == expected_status_code
    assert response.json() == {"detail": expected_detail}


@pytest.mark.anyio
async def test_netbox_client_maps_netbox_sites_with_nullable_fields():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Token netbox-token"

        if request.url.path == "/api/dcim/sites/" and not request.url.query:
            return httpx.Response(
                200,
                json={
                    "next": "https://netbox.example.com/api/dcim/sites/?page=2",
                    "results": [
                        {
                            "id": 1,
                            "facility": "AKL-01",
                            "name": "Auckland",
                            "description": "Primary site",
                            "latitude": -36.8485,
                            "longitude": 174.7633,
                            "custom_fields": {},
                        }
                    ],
                },
            )

        return httpx.Response(
            200,
            json={
                "next": None,
                "results": [
                    {
                        "id": 2,
                        "facility": None,
                        "name": "Wellington",
                        "description": "",
                        "latitude": None,
                        "longitude": None,
                        "custom_fields": {},
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    netbox_http_client = httpx.AsyncClient(
        transport=transport,
        base_url="https://netbox.example.com",
    )
    client = NetBoxClient(
        DummySettings(),
        client=netbox_http_client,
    )

    try:
        feed = await client.fetch_feed()
    finally:
        await netbox_http_client.aclose()

    assert feed.schema_version == "1.0"
    assert feed.source_name == "netbox"
    assert len(feed.sites) == 2
    assert feed.sites[0].model_dump() == {
        "external_id": "1",
        "code": "AKL-01",
        "name": "Auckland",
        "description": "Primary site",
        "latitude": -36.8485,
        "longitude": 174.7633,
        "tags": [],
    }
    assert feed.sites[1].model_dump() == {
        "external_id": "2",
        "code": None,
        "name": "Wellington",
        "description": None,
        "latitude": None,
        "longitude": None,
        "tags": [],
    }


@pytest.mark.anyio
async def test_netbox_client_applies_site_filter_to_first_sites_request_only():
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))

        if (
            request.url.path == "/api/dcim/sites/"
            and request.url.query == b"status=active&cf_storage_location=false"
        ):
            return httpx.Response(
                200,
                json={
                    "next": "https://netbox.example.com/api/dcim/sites/?page=2",
                    "results": [
                        {
                            "id": 1,
                            "facility": None,
                            "name": "Auckland",
                            "description": "",
                            "latitude": None,
                            "longitude": None,
                            "custom_fields": {},
                        }
                    ],
                },
            )

        if request.url.path == "/api/dcim/sites/" and request.url.query == b"page=2":
            return httpx.Response(
                200,
                json={
                    "next": None,
                    "results": [
                        {
                            "id": 2,
                            "facility": None,
                            "name": "Wellington",
                            "description": "",
                            "latitude": None,
                            "longitude": None,
                            "custom_fields": {},
                        }
                    ],
                },
            )

        raise AssertionError(f"Unexpected request URL: {request.url}")

    transport = httpx.MockTransport(handler)
    netbox_http_client = httpx.AsyncClient(
        transport=transport,
        base_url="https://netbox.example.com",
    )
    client = NetBoxClient(
        FilterSettings(),
        client=netbox_http_client,
    )

    try:
        feed = await client.fetch_feed()
    finally:
        await netbox_http_client.aclose()

    assert requested_urls == [
        "https://netbox.example.com/api/dcim/sites/?status=active&cf_storage_location=false",
        "https://netbox.example.com/api/dcim/sites/?page=2",
    ]
    assert [site.external_id for site in feed.sites] == ["1", "2"]


@pytest.mark.anyio
async def test_netbox_client_preserves_url_encoded_site_filter():
    requested_url = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requested_url
        requested_url = str(request.url)
        return httpx.Response(
            200,
            json={
                "next": None,
                "results": [
                    {
                        "id": 1,
                        "facility": None,
                        "name": "Main Site",
                        "description": "",
                        "latitude": None,
                        "longitude": None,
                        "custom_fields": {},
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    netbox_http_client = httpx.AsyncClient(
        transport=transport,
        base_url="https://netbox.example.com",
    )
    client = NetBoxClient(
        EncodedFilterSettings(),
        client=netbox_http_client,
    )

    try:
        await client.fetch_feed()
    finally:
        await netbox_http_client.aclose()

    assert requested_url == (
        "https://netbox.example.com/api/dcim/sites/?status=active&q=Main%20Site"
    )


@pytest.mark.anyio
async def test_netbox_client_maps_configured_custom_fields_to_tags():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/dcim/sites/":
            return httpx.Response(
                200,
                json={
                    "next": None,
                    "results": [
                        {
                            "id": 1,
                            "facility": "AKL-01",
                            "name": "Auckland",
                            "description": "Primary site",
                            "latitude": -36.8485,
                            "longitude": 174.7633,
                            "custom_fields": {
                                "test": ["111"],
                                "plain": "remote",
                                "missing": None,
                                "ignored": "not-configured",
                            },
                        }
                    ],
                },
            )

        if request.url.path == "/api/extras/custom-fields/":
            return httpx.Response(
                200,
                json={
                    "next": None,
                    "results": [
                        {
                            "name": "test",
                            "object_types": ["dcim.site"],
                            "choice_set": {
                                "url": "https://netbox.example.com/api/extras/custom-field-choice-sets/1/"
                            },
                        },
                        {
                            "name": "plain",
                            "object_types": ["dcim.site"],
                            "choice_set": None,
                        },
                    ],
                },
            )

        if request.url.path == "/api/extras/custom-field-choice-sets/1/":
            return httpx.Response(
                200,
                json={
                    "extra_choices": [["111", "first Choice"]],
                    "choice_colors": {"111": "red"},
                },
            )

        raise AssertionError(f"Unexpected request path: {request.url.path}")

    transport = httpx.MockTransport(handler)
    netbox_http_client = httpx.AsyncClient(
        transport=transport,
        base_url="https://netbox.example.com",
    )
    client = NetBoxClient(
        TagSettings(),
        client=netbox_http_client,
    )

    try:
        feed = await client.fetch_feed()
    finally:
        await netbox_http_client.aclose()

    assert feed.schema_version == "1.0"
    assert feed.sites[0].model_dump()["tags"] == [
        {"label": "first Choice", "color": "red"},
        {"label": "remote", "color": None},
    ]


@pytest.mark.anyio
async def test_netbox_client_uses_inline_tag_label_and_color_when_present():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/dcim/sites/":
            return httpx.Response(
                200,
                json={
                    "next": None,
                    "results": [
                        {
                            "id": 1,
                            "facility": None,
                            "name": "Auckland",
                            "description": "",
                            "latitude": None,
                            "longitude": None,
                            "custom_fields": {
                                "test": [
                                    {
                                        "value": "critical",
                                        "label": "Critical",
                                        "color": "orange",
                                    }
                                ]
                            },
                        }
                    ],
                },
            )

        if request.url.path == "/api/extras/custom-fields/":
            return httpx.Response(
                200,
                json={
                    "next": None,
                    "results": [
                        {
                            "name": "test",
                            "object_types": ["dcim.site"],
                            "choice_set": None,
                        }
                    ],
                },
            )

        raise AssertionError(f"Unexpected request path: {request.url.path}")

    transport = httpx.MockTransport(handler)
    netbox_http_client = httpx.AsyncClient(
        transport=transport,
        base_url="https://netbox.example.com",
    )
    client = NetBoxClient(
        TagSettings(),
        client=netbox_http_client,
    )

    try:
        feed = await client.fetch_feed()
    finally:
        await netbox_http_client.aclose()

    assert feed.sites[0].model_dump()["tags"] == [
        {"label": "Critical", "color": "orange"}
    ]


@pytest.mark.anyio
async def test_netbox_client_handles_missing_custom_fields_when_tags_configured():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/dcim/sites/":
            return httpx.Response(
                200,
                json={
                    "next": None,
                    "results": [
                        {
                            "id": 1,
                            "facility": None,
                            "name": "Auckland",
                            "description": "",
                            "latitude": None,
                            "longitude": None,
                        }
                    ],
                },
            )

        if request.url.path == "/api/extras/custom-fields/":
            return httpx.Response(
                200,
                json={
                    "next": None,
                    "results": [
                        {
                            "name": "test",
                            "object_types": ["dcim.site"],
                            "choice_set": None,
                        }
                    ],
                },
            )

        raise AssertionError(f"Unexpected request path: {request.url.path}")

    transport = httpx.MockTransport(handler)
    netbox_http_client = httpx.AsyncClient(
        transport=transport,
        base_url="https://netbox.example.com",
    )
    client = NetBoxClient(
        SingleTagFieldSettings(),
        client=netbox_http_client,
    )

    try:
        feed = await client.fetch_feed()
    finally:
        await netbox_http_client.aclose()

    assert feed.sites[0].tags == []


@pytest.mark.anyio
async def test_netbox_client_follows_custom_field_pagination():
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(str(request.url))

        if request.url.path == "/api/dcim/sites/":
            return httpx.Response(
                200,
                json={
                    "next": None,
                    "results": [
                        {
                            "id": 1,
                            "facility": None,
                            "name": "Auckland",
                            "description": "",
                            "latitude": None,
                            "longitude": None,
                            "custom_fields": {"test": "111"},
                        }
                    ],
                },
            )

        if request.url.path == "/api/extras/custom-fields/" and not request.url.query:
            return httpx.Response(
                200,
                json={
                    "next": (
                        "https://netbox.example.com/api/extras/custom-fields/?page=2"
                    ),
                    "results": [
                        {
                            "name": "other",
                            "object_types": ["dcim.site"],
                            "choice_set": None,
                        }
                    ],
                },
            )

        if request.url.path == "/api/extras/custom-fields/" and request.url.query:
            return httpx.Response(
                200,
                json={
                    "next": None,
                    "results": [
                        {
                            "name": "test",
                            "object_types": ["dcim.site"],
                            "choice_set": {
                                "url": "https://netbox.example.com/api/extras/custom-field-choice-sets/1/"
                            },
                        }
                    ],
                },
            )

        if request.url.path == "/api/extras/custom-field-choice-sets/1/":
            return httpx.Response(
                200,
                json={
                    "extra_choices": [["111", "Primary"]],
                    "choice_colors": {"111": "blue"},
                },
            )

        raise AssertionError(f"Unexpected request path: {request.url.path}")

    transport = httpx.MockTransport(handler)
    netbox_http_client = httpx.AsyncClient(
        transport=transport,
        base_url="https://netbox.example.com",
    )
    client = NetBoxClient(
        SingleTagFieldSettings(),
        client=netbox_http_client,
    )

    try:
        feed = await client.fetch_feed()
    finally:
        await netbox_http_client.aclose()

    assert any("/api/extras/custom-fields/?page=2" in url for url in requested_paths)
    assert feed.sites[0].model_dump()["tags"] == [{"label": "Primary", "color": "blue"}]


@pytest.mark.anyio
async def test_netbox_client_ignores_custom_fields_not_attached_to_sites():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/dcim/sites/":
            return httpx.Response(
                200,
                json={
                    "next": None,
                    "results": [
                        {
                            "id": 1,
                            "facility": None,
                            "name": "Auckland",
                            "description": "",
                            "latitude": None,
                            "longitude": None,
                            "custom_fields": {"test": "111"},
                        }
                    ],
                },
            )

        if request.url.path == "/api/extras/custom-fields/":
            return httpx.Response(
                200,
                json={
                    "next": None,
                    "results": [
                        {
                            "name": "test",
                            "object_types": ["dcim.device"],
                            "choice_set": {
                                "url": "https://netbox.example.com/api/extras/custom-field-choice-sets/1/"
                            },
                        }
                    ],
                },
            )

        raise AssertionError(f"Unexpected request path: {request.url.path}")

    transport = httpx.MockTransport(handler)
    netbox_http_client = httpx.AsyncClient(
        transport=transport,
        base_url="https://netbox.example.com",
    )
    client = NetBoxClient(
        SingleTagFieldSettings(),
        client=netbox_http_client,
    )

    try:
        feed = await client.fetch_feed()
    finally:
        await netbox_http_client.aclose()

    assert feed.sites[0].tags == []


@pytest.mark.anyio
async def test_netbox_client_skips_unusable_tag_values():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/dcim/sites/":
            return httpx.Response(
                200,
                json={
                    "next": None,
                    "results": [
                        {
                            "id": 1,
                            "facility": None,
                            "name": "Auckland",
                            "description": "",
                            "latitude": None,
                            "longitude": None,
                            "custom_fields": {
                                "test": [
                                    None,
                                    "",
                                    {},
                                    {"color": "red"},
                                    {"value": "valid", "label": "Valid"},
                                ]
                            },
                        }
                    ],
                },
            )

        if request.url.path == "/api/extras/custom-fields/":
            return httpx.Response(
                200,
                json={
                    "next": None,
                    "results": [
                        {
                            "name": "test",
                            "object_types": ["dcim.site"],
                            "choice_set": None,
                        }
                    ],
                },
            )

        raise AssertionError(f"Unexpected request path: {request.url.path}")

    transport = httpx.MockTransport(handler)
    netbox_http_client = httpx.AsyncClient(
        transport=transport,
        base_url="https://netbox.example.com",
    )
    client = NetBoxClient(
        SingleTagFieldSettings(),
        client=netbox_http_client,
    )

    try:
        feed = await client.fetch_feed()
    finally:
        await netbox_http_client.aclose()

    assert feed.sites[0].model_dump()["tags"] == [{"label": "Valid", "color": None}]


@pytest.mark.anyio
async def test_netbox_client_translates_custom_field_http_errors():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/dcim/sites/":
            return httpx.Response(
                200,
                json={
                    "next": None,
                    "results": [
                        {
                            "id": 1,
                            "facility": None,
                            "name": "Auckland",
                            "description": "",
                            "latitude": None,
                            "longitude": None,
                            "custom_fields": {},
                        }
                    ],
                },
            )

        if request.url.path == "/api/extras/custom-fields/":
            return httpx.Response(403, request=request)

        raise AssertionError(f"Unexpected request path: {request.url.path}")

    transport = httpx.MockTransport(handler)
    netbox_http_client = httpx.AsyncClient(
        transport=transport,
        base_url="https://netbox.example.com",
    )
    client = NetBoxClient(
        SingleTagFieldSettings(),
        client=netbox_http_client,
    )

    try:
        with pytest.raises(NetBoxUpstreamHTTPError) as exc_info:
            await client.fetch_feed()
    finally:
        await netbox_http_client.aclose()

    assert exc_info.value.response.status_code == 403


@pytest.mark.anyio
async def test_netbox_client_rejects_invalid_choice_set_payload():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/dcim/sites/":
            return httpx.Response(
                200,
                json={
                    "next": None,
                    "results": [
                        {
                            "id": 1,
                            "facility": None,
                            "name": "Auckland",
                            "description": "",
                            "latitude": None,
                            "longitude": None,
                            "custom_fields": {"test": "111"},
                        }
                    ],
                },
            )

        if request.url.path == "/api/extras/custom-fields/":
            return httpx.Response(
                200,
                json={
                    "next": None,
                    "results": [
                        {
                            "name": "test",
                            "object_types": ["dcim.site"],
                            "choice_set": {
                                "url": "https://netbox.example.com/api/extras/custom-field-choice-sets/1/"
                            },
                        }
                    ],
                },
            )

        if request.url.path == "/api/extras/custom-field-choice-sets/1/":
            return httpx.Response(
                200,
                json={"extra_choices": "not-a-list", "choice_colors": {}},
            )

        raise AssertionError(f"Unexpected request path: {request.url.path}")

    transport = httpx.MockTransport(handler)
    netbox_http_client = httpx.AsyncClient(
        transport=transport,
        base_url="https://netbox.example.com",
    )
    client = NetBoxClient(
        SingleTagFieldSettings(),
        client=netbox_http_client,
    )

    try:
        with pytest.raises(NetBoxUpstreamPayloadError):
            await client.fetch_feed()
    finally:
        await netbox_http_client.aclose()


def test_verify_bearer_token_accepts_expected_token():
    assert (
        verify_bearer_token(
            authorization="Bearer atlas-token",
            settings=DummySettings(),
        )
        is None
    )


def test_verify_bearer_token_rejects_missing_token():
    with pytest.raises(HTTPException) as exc_info:
        verify_bearer_token(
            authorization=None,
            settings=DummySettings(),
        )

    assert exc_info.value.status_code == 401


def test_verify_bearer_token_rejects_wrong_token():
    with pytest.raises(HTTPException) as exc_info:
        verify_bearer_token(
            authorization="Bearer wrong-token",
            settings=DummySettings(),
        )

    assert exc_info.value.status_code == 401


def test_access_atlas_feed_serializes_generated_at_as_utc_z():
    feed = AccessAtlasFeed(
        schema_version="1.0",
        source_name="netbox",
        generated_at="2026-05-04T10:11:12+00:00",
        sites=[],
    )

    assert feed.model_dump(mode="json")["generated_at"] == "2026-05-04T10:11:12Z"


@pytest.mark.anyio
async def test_get_site_feed_translates_upstream_http_errors():
    request = httpx.Request("GET", "http://localhost:8000/api/dcim/sites/")
    response = httpx.Response(403, request=request)

    class FailingNetBoxClient:
        async def fetch_feed(self):
            raise NetBoxUpstreamHTTPError(response)

    with pytest.raises(HTTPException) as exc_info:
        await get_site_feed(
            client=FailingNetBoxClient(),
        )

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == "Upstream NetBox request failed"


@pytest.mark.anyio
async def test_get_site_feed_translates_upstream_connection_errors():
    class FailingNetBoxClient:
        async def fetch_feed(self):
            raise NetBoxUpstreamConnectionError("Failed to connect to NetBox")

    with pytest.raises(HTTPException) as exc_info:
        await get_site_feed(
            client=FailingNetBoxClient(),
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "NetBox is unavailable"


@pytest.mark.anyio
async def test_get_site_feed_translates_upstream_payload_errors():
    class FailingNetBoxClient:
        async def fetch_feed(self):
            raise NetBoxUpstreamPayloadError("NetBox returned an invalid payload")

    with pytest.raises(HTTPException) as exc_info:
        await get_site_feed(
            client=FailingNetBoxClient(),
        )

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == "Upstream NetBox returned an invalid payload"


@pytest.mark.anyio
async def test_netbox_client_rejects_invalid_json_payload():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"{invalid-json",
            headers={"Content-Type": "application/json"},
        )

    transport = httpx.MockTransport(handler)
    netbox_http_client = httpx.AsyncClient(
        transport=transport,
        base_url="https://netbox.example.com",
    )
    client = NetBoxClient(
        DummySettings(),
        client=netbox_http_client,
    )

    try:
        with pytest.raises(NetBoxUpstreamPayloadError):
            await client.fetch_feed()
    finally:
        await netbox_http_client.aclose()


@pytest.mark.anyio
async def test_netbox_client_rejects_unexpected_payload_shape():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "next": None,
                "results": [
                    {
                        "id": 1,
                        "facility": "AKL-01",
                        "name": None,
                        "description": "Primary site",
                        "latitude": -36.8485,
                        "longitude": 174.7633,
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    netbox_http_client = httpx.AsyncClient(
        transport=transport,
        base_url="https://netbox.example.com",
    )
    client = NetBoxClient(
        DummySettings(),
        client=netbox_http_client,
    )

    try:
        with pytest.raises(NetBoxUpstreamPayloadError):
            await client.fetch_feed()
    finally:
        await netbox_http_client.aclose()
