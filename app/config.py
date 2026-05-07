from functools import lru_cache

from pydantic import Field
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
    tag_custom_fields_raw: str = Field("", alias="NETBOX_TAG_CUSTOM_FIELDS")
    debug: bool = Field(False, alias="DEBUG")

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
