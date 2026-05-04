from datetime import UTC, datetime
from json import JSONDecodeError
from urllib.parse import urljoin

import httpx
from pydantic import ValidationError

from app.config import NETBOX_API_PATH, SITE_FEED_SCHEMA_VERSION, SOURCE_NAME, Settings
from app.models import AccessAtlasFeed, AccessAtlasSite, NetBoxPage, NetBoxSite


class NetBoxUpstreamHTTPError(Exception):
    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        super().__init__(
            f"NetBox upstream request failed with status {response.status_code}"
        )


class NetBoxUpstreamConnectionError(Exception):
    pass


class NetBoxUpstreamPayloadError(Exception):
    pass


class NetBoxClient:
    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self._client = client

    async def fetch_feed(self) -> AccessAtlasFeed:
        sites = await self.fetch_sites()
        mapped_sites = [self._map_site(site) for site in sites]
        return AccessAtlasFeed(
            schema_version=SITE_FEED_SCHEMA_VERSION,
            source_name=SOURCE_NAME,
            generated_at=datetime.now(UTC),
            sites=mapped_sites,
        )

    async def fetch_sites(self) -> list[NetBoxSite]:
        page_url = urljoin(self.settings.netbox_url, NETBOX_API_PATH)
        collected: list[NetBoxSite] = []

        async with self._get_client() as client:
            while page_url:
                try:
                    response = await client.get(
                        page_url,
                        headers=self._request_headers(),
                    )
                    response.raise_for_status()
                    payload = NetBoxPage.model_validate(response.json())
                except httpx.HTTPStatusError as exc:
                    raise NetBoxUpstreamHTTPError(exc.response) from exc
                except httpx.RequestError as exc:
                    raise NetBoxUpstreamConnectionError(
                        "Failed to connect to NetBox"
                    ) from exc
                except (JSONDecodeError, ValidationError) as exc:
                    raise NetBoxUpstreamPayloadError(
                        "NetBox returned an invalid payload"
                    ) from exc

                collected.extend(payload.results)
                page_url = payload.next

        return collected

    def _request_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Token {self.settings.netbox_token}",
            "Accept": "application/json",
        }

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client

        return httpx.AsyncClient(
            headers=self._request_headers(),
            timeout=httpx.Timeout(30.0),
        )

    @staticmethod
    def _map_site(site: NetBoxSite) -> AccessAtlasSite:
        return AccessAtlasSite(
            external_id=str(site.id),
            code=site.facility,
            name=site.name,
            description=site.description or None,
            latitude=site.latitude,
            longitude=site.longitude,
        )
