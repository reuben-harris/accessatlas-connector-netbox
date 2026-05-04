from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, field_serializer


class AccessAtlasSite(BaseModel):
    external_id: str
    code: str | None
    name: str
    description: str | None
    latitude: float | None
    longitude: float | None


class AccessAtlasFeed(BaseModel):
    schema_version: str
    source_name: str
    generated_at: datetime
    sites: list[AccessAtlasSite]

    model_config = ConfigDict()

    @field_serializer("generated_at")
    def serialize_generated_at(self, value: datetime) -> str:
        return (
            value.astimezone(UTC)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )


class NetBoxSite(BaseModel):
    id: int
    facility: str | None = None
    name: str
    description: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class NetBoxPage(BaseModel):
    next: str | None
    results: list[NetBoxSite]
