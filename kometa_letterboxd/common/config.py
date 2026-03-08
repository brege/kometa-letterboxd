"""Configuration models and path helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class KometaConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    config_path: NonEmptyStr | None = None


class DatedConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kometa_destination: NonEmptyStr = Field(
        validation_alias=AliasChoices("kometa_destination", "kometa_target")
    )
    letterboxd_prefix: str = ""
    plex_prefix: str = ""
    days_before: int = 0
    collection_extra: dict[str, object] = Field(default_factory=dict)
    extended_extra: dict[str, object] = Field(default_factory=dict)


class TaggedConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    tag: str = ""
    extra: dict[str, object] = Field(default_factory=dict)


class ShowdownConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    showdown_json: NonEmptyStr
    library: str | None = None
    threshold: int = Field(default=4, ge=1)
    sort: Literal["matches_desc", "matches_asc", "none"] = "matches_desc"
    window: int = Field(default=5, ge=1)
    label: NonEmptyStr = "Showdown Spotlight"
    state_file: NonEmptyStr | None = None
    asset_directory: NonEmptyStr | None = None
    kometa_destination: NonEmptyStr | None = None


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    username: NonEmptyStr
    request_timeout: int = Field(default=30, ge=1)
    lists_cache: NonEmptyStr | None = None
    refresh_lists: bool = False
    kometa: KometaConfig = Field(default_factory=KometaConfig)
    dated: DatedConfig
    tagged: TaggedConfig = Field(default_factory=TaggedConfig)
    showdown: ShowdownConfig | None = None

    @model_validator(mode="after")
    def validate_showdown_config(self) -> AppConfig:
        if self.showdown is not None and self.kometa.config_path is None:
            raise ValueError("showdown requires kometa.config_path")
        return self


def load_config(path: Path) -> AppConfig:
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    return AppConfig.model_validate(payload)


def resolve_path(raw: str | Path | None, base_path: Path) -> Path | None:
    if raw is None:
        return None
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = (base_path / candidate).resolve()
    return candidate
