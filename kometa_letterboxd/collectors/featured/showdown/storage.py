"""File helpers for the showdown collector."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from kometa_letterboxd.common.config import resolve_path


class ShowdownState(BaseModel):
    model_config = ConfigDict(extra="ignore")

    window_position: int = 0
    collection_lifecycles: dict[str, Literal["spotlight", "library", "retire"]] = Field(
        default_factory=dict
    )
    collection_titles: dict[str, str] = Field(default_factory=dict)


def load_showdown_datasets(path: Path) -> list[Mapping[str, Any]]:
    """Load showdown datasets from the cached JSON payload."""

    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    if isinstance(payload, dict) and "showdowns" in payload:
        payload = payload.get("showdowns")

    if not isinstance(payload, Sequence):
        raise ValueError(f"Unexpected showdown dataset structure in {path}")

    datasets: list[Mapping[str, Any]] = []
    for item in payload:
        if not isinstance(item, Mapping):
            raise ValueError(f"Unexpected showdown dataset entry in {path}")
        datasets.append(item)
    return datasets


def load_showdown_cache(path: Path) -> dict[str, dict[str, Any]]:
    """Load showdown datasets keyed by slug for cache reuse."""

    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    entries: Sequence[Mapping[str, Any]]
    if isinstance(payload, dict) and "showdowns" in payload:
        raw_entries = payload.get("showdowns")
        if not isinstance(raw_entries, Sequence):
            raise ValueError(f"Unexpected showdown cache structure in {path}")
        entries = raw_entries
    elif isinstance(payload, Sequence):
        entries = payload
    else:
        raise ValueError(f"Unexpected showdown cache structure in {path}")

    cache: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise ValueError(f"Unexpected showdown cache entry in {path}")
        summary = entry.get("summary")
        if not isinstance(summary, Mapping):
            raise ValueError(f"Unexpected showdown cache summary in {path}")
        slug = summary.get("slug")
        if not slug:
            raise ValueError(f"Missing showdown cache slug in {path}")
        cache[str(slug)] = dict(entry)
    return cache


def save_showdown_cache(path: Path, cache: Mapping[str, Mapping[str, Any]]) -> None:
    """Persist showdown cache in the expected JSON structure."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"showdowns": [dict(value) for value in cache.values()]}
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=False)


def load_state(path: Path) -> ShowdownState:
    """Load showdown rotation state from disk."""

    if not path.exists():
        return ShowdownState()
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return ShowdownState.model_validate(data)


def save_state(path: Path, data: ShowdownState) -> None:
    """Persist showdown rotation state to disk."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(
            data.model_dump(),
            handle,
            indent=2,
            sort_keys=True,
        )


__all__ = [
    "ShowdownState",
    "load_showdown_cache",
    "load_showdown_datasets",
    "load_state",
    "resolve_path",
    "save_showdown_cache",
    "save_state",
]
