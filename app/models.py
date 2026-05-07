from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_serializer


class AccessAtlasTag(BaseModel):
    label: str
    color: str | None = None


class AccessAtlasSite(BaseModel):
    external_id: str
    code: str | None
    name: str
    description: str | None
    latitude: float | None
    longitude: float | None
    tags: list[AccessAtlasTag] = Field(default_factory=list)


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
    custom_fields: dict[str, Any] = Field(default_factory=dict)


class NetBoxPage(BaseModel):
    next: str | None
    results: list[NetBoxSite]


class NetBoxChoiceSetReference(BaseModel):
    url: str


class NetBoxCustomField(BaseModel):
    name: str
    object_types: list[str] = Field(default_factory=list)
    choice_set: NetBoxChoiceSetReference | None = None


class NetBoxCustomFieldPage(BaseModel):
    next: str | None
    results: list[NetBoxCustomField]


class NetBoxCustomFieldChoiceSet(BaseModel):
    extra_choices: list[tuple[str, str]] = Field(default_factory=list)
    choice_colors: dict[str, str] = Field(default_factory=dict)
