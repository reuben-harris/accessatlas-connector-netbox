import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from json import JSONDecodeError
from urllib.parse import urljoin

import httpx
from pydantic import ValidationError

from app.config import NETBOX_API_PATH, SITE_FEED_SCHEMA_VERSION, SOURCE_NAME, Settings
from app.models import (
    AccessAtlasFeed,
    AccessAtlasSite,
    AccessAtlasTag,
    NetBoxCustomFieldChoiceSet,
    NetBoxCustomFieldPage,
    NetBoxPage,
    NetBoxSite,
)

CUSTOM_FIELDS_API_PATH = "/api/extras/custom-fields/"
NETBOX_SITE_OBJECT_TYPE = "dcim.site"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NetBoxTagChoice:
    label: str
    color: str | None = None


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
        async with self._client_context() as client:
            sites = await self._fetch_sites(client)
            tag_choices = await self._fetch_tag_choices(client)

        mapped_sites = [self._map_site(site, tag_choices) for site in sites]
        logger.info(
            "Mapped NetBox sites to Access Atlas feed",
            extra={
                "site_count": len(mapped_sites),
                "tag_count": sum(len(site.tags) for site in mapped_sites),
                "configured_tag_fields": self.settings.tag_custom_fields,
                "choice_metadata_fields": sorted(tag_choices),
            },
        )
        return AccessAtlasFeed(
            schema_version=SITE_FEED_SCHEMA_VERSION,
            source_name=SOURCE_NAME,
            generated_at=datetime.now(UTC),
            sites=mapped_sites,
        )

    async def fetch_sites(self) -> list[NetBoxSite]:
        async with self._client_context() as client:
            return await self._fetch_sites(client)

    async def _fetch_sites(self, client: httpx.AsyncClient) -> list[NetBoxSite]:
        page_url = self._site_list_url()
        collected: list[NetBoxSite] = []

        while page_url:
            payload = await self._request_model(client, page_url, NetBoxPage)
            collected.extend(payload.results)
            page_url = payload.next

        logger.info("Fetched NetBox sites", extra={"site_count": len(collected)})
        return collected

    def _site_list_url(self) -> str:
        site_list_url = urljoin(self.settings.netbox_url, NETBOX_API_PATH)
        if not self.settings.netbox_site_filter:
            return site_list_url

        return f"{site_list_url}?{self.settings.netbox_site_filter}"

    async def _fetch_tag_choices(
        self,
        client: httpx.AsyncClient,
    ) -> dict[str, dict[str, NetBoxTagChoice]]:
        configured_fields = set(self.settings.tag_custom_fields)
        if not configured_fields:
            logger.info("No NetBox custom fields configured for tags")
            return {}

        logger.info(
            "Loading NetBox custom fields for tags",
            extra={"custom_fields": sorted(configured_fields)},
        )

        field_choices: dict[str, dict[str, NetBoxTagChoice]] = {}
        found_fields: set[str] = set()
        page_url = urljoin(self.settings.netbox_url, CUSTOM_FIELDS_API_PATH)

        while page_url:
            payload = await self._request_model(
                client,
                page_url,
                NetBoxCustomFieldPage,
            )

            for custom_field in payload.results:
                if custom_field.name not in configured_fields:
                    continue

                if NETBOX_SITE_OBJECT_TYPE not in custom_field.object_types:
                    continue

                found_fields.add(custom_field.name)

                if custom_field.choice_set is None:
                    field_choices[custom_field.name] = {}
                    continue

                choice_set = await self._request_model(
                    client,
                    custom_field.choice_set.url,
                    NetBoxCustomFieldChoiceSet,
                )
                field_choices[custom_field.name] = {
                    value: NetBoxTagChoice(
                        label=label,
                        color=choice_set.choice_colors.get(value),
                    )
                    for value, label in choice_set.extra_choices
                }

            page_url = payload.next

        for missing_field in sorted(configured_fields - found_fields):
            logger.warning(
                "Configured NetBox tag custom field was not found",
                extra={"custom_field": missing_field},
            )

        logger.info(
            "Loaded NetBox tag choice metadata",
            extra={"custom_field_count": len(field_choices)},
        )
        return field_choices

    async def _request_model(
        self,
        client: httpx.AsyncClient,
        url: str,
        model: type[NetBoxPage]
        | type[NetBoxCustomFieldPage]
        | type[NetBoxCustomFieldChoiceSet],
    ):
        try:
            response = await client.get(
                url,
                headers=self._request_headers(),
            )
            response.raise_for_status()
            return model.model_validate(response.json())
        except httpx.HTTPStatusError as exc:
            raise NetBoxUpstreamHTTPError(exc.response) from exc
        except httpx.RequestError as exc:
            raise NetBoxUpstreamConnectionError("Failed to connect to NetBox") from exc
        except (JSONDecodeError, ValidationError) as exc:
            raise NetBoxUpstreamPayloadError(
                "NetBox returned an invalid payload"
            ) from exc

    @asynccontextmanager
    async def _client_context(self):
        if self._client is not None:
            yield self._client
            return

        async with httpx.AsyncClient(
            headers=self._request_headers(),
            timeout=httpx.Timeout(30.0),
        ) as client:
            yield client

    def _request_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Token {self.settings.netbox_token}",
            "Accept": "application/json",
        }

    def _map_site(
        self,
        site: NetBoxSite,
        tag_choices: dict[str, dict[str, NetBoxTagChoice]],
    ) -> AccessAtlasSite:
        return AccessAtlasSite(
            external_id=str(site.id),
            code=site.facility,
            name=site.name,
            description=site.description or None,
            latitude=site.latitude,
            longitude=site.longitude,
            tags=self._extract_tags(site, tag_choices),
        )

    def _extract_tags(
        self,
        site: NetBoxSite,
        tag_choices: dict[str, dict[str, NetBoxTagChoice]],
    ) -> list[AccessAtlasTag]:
        tags: list[AccessAtlasTag] = []

        for field_name in self.settings.tag_custom_fields:
            if field_name not in tag_choices:
                continue

            raw_value = site.custom_fields.get(field_name)
            if raw_value in (None, "", []):
                continue

            values = raw_value if isinstance(raw_value, list) else [raw_value]
            for value in values:
                tag = self._build_tag(field_name, value, tag_choices)
                if tag is not None:
                    tags.append(tag)

        logger.debug(
            "Mapped NetBox custom fields to Access Atlas tags",
            extra={
                "site_id": site.id,
                "tag_labels": [tag.label for tag in tags],
            },
        )
        return tags

    @staticmethod
    def _build_tag(
        field_name: str,
        value: object,
        tag_choices: dict[str, dict[str, NetBoxTagChoice]],
    ) -> AccessAtlasTag | None:
        normalized_value = NetBoxClient._normalize_tag_value(value)
        if normalized_value is None:
            return None

        raw_value, label, color = normalized_value
        choice = tag_choices.get(field_name, {}).get(raw_value)

        if choice is not None:
            label = choice.label
            color = choice.color or color

        return AccessAtlasTag(label=label, color=color)

    @staticmethod
    def _normalize_tag_value(value: object) -> tuple[str, str, str | None] | None:
        if isinstance(value, dict):
            raw_value = NetBoxClient._first_present_value(
                value.get("value"),
                value.get("id"),
                value.get("name"),
                value.get("label"),
                value.get("display"),
            )
            if raw_value is None:
                return None

            label = NetBoxClient._first_present_value(
                value.get("label"),
                value.get("display"),
                value.get("value"),
                raw_value,
            )
            color = value.get("color")

            return str(raw_value), str(label), str(color) if color else None

        if value in (None, "", []):
            return None

        return str(value), str(value), None

    @staticmethod
    def _first_present_value(*values: object) -> object | None:
        for value in values:
            if value is not None and value != "":
                return value

        return None
