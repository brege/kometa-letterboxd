"""JSON cache helpers for Letterboxd list metadata."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any


def load_lists(cache_path: str | Path) -> list[Mapping[str, Any]]:
    """Return cached Letterboxd lists if the cache exists."""

    path = Path(cache_path).expanduser()
    if not path.exists():
        return []

    with path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)

    if isinstance(raw, dict):
        raw = raw.get("lists", [])
    if not isinstance(raw, list):
        raise ValueError(f"Unexpected cache structure in {path}")

    result: list[Mapping[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            raise ValueError(f"Unexpected cache entry in {path}")
        if not entry.get("title") or not entry.get("url_suffix"):
            raise ValueError(f"Incomplete cache entry in {path}")
        result.append(entry)
    return result


def save_lists(cache_path: str | Path, lists: Iterable[Mapping[str, Any]]) -> None:
    """Persist Letterboxd lists so subsequent runs can skip HTTP fetches."""

    path = Path(cache_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "lists": [
            {
                "title": entry.get("title"),
                "url_suffix": entry.get("url_suffix"),
                "tags": list(entry.get("tags", [])),
            }
            for entry in lists
            if entry.get("title") and entry.get("url_suffix")
        ]
    }

    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
