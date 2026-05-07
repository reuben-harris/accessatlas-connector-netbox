from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

NETBOX_API_PATH = "/api/dcim/sites/"
SOURCE_NAME = "netbox"
SITE_FEED_SCHEMA_VERSION = "1.0"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    netbox_url: str = Field(alias="NETBOX_URL")
    netbox_token: str = Field(alias="NETBOX_TOKEN")
    access_atlas_token: str = Field(alias="ACCESS_ATLAS_TOKEN")
    netbox_site_filter: str = Field("", alias="NETBOX_SITE_FILTER")
    tag_custom_fields_raw: str = Field("", alias="NETBOX_TAG_CUSTOM_FIELDS")
    debug: bool = Field(False, alias="DEBUG")

    @field_validator("netbox_site_filter")
    @classmethod
    def validate_netbox_site_filter(cls, value: str) -> str:
        if value.startswith("?"):
            raise ValueError(
                "NETBOX_SITE_FILTER must be a query string without a leading '?'"
            )

        if "://" in value or value.startswith("/"):
            raise ValueError(
                "NETBOX_SITE_FILTER must only contain query string parameters"
            )

        return value

    @property
    def tag_custom_fields(self) -> list[str]:
        return [
            custom_field.strip()
            for custom_field in self.tag_custom_fields_raw.split(",")
            if custom_field.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()
