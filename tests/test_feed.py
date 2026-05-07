import httpx
import pytest
from fastapi import HTTPException

from app.main import get_site_feed, verify_bearer_token
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
    port = 8000
    log_level = "info"


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
    }
    assert feed.sites[1].model_dump() == {
        "external_id": "2",
        "code": None,
        "name": "Wellington",
        "description": None,
        "latitude": None,
        "longitude": None,
    }


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
