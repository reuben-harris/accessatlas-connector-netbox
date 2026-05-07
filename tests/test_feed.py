from datetime import UTC, datetime

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

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
    tag_custom_fields: list[str] = []


class TagSettings(DummySettings):
    tag_custom_fields = ["test", "plain", "missing"]


class SingleTagFieldSettings(DummySettings):
    tag_custom_fields = ["test"]


def test_settings_parse_tag_fields_debug_and_ignore_unrelated_values():
    settings = Settings(
        NETBOX_URL="https://netbox.example.com",
        NETBOX_TOKEN="netbox-token",
        ACCESS_ATLAS_TOKEN="atlas-token",
        NETBOX_TAG_CUSTOM_FIELDS="test, plain, ,",
        DEBUG="true",
        GITHUB_TOKEN="ignored",
    )

    assert settings.tag_custom_fields == ["test", "plain"]
    assert settings.debug is True


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
