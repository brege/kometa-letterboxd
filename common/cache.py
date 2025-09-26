"""JSON cache helpers for Letterboxd list metadata."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, List, Mapping


def load_lists(cache_path: str | Path) -> List[Mapping[str, object]]:
    """Return cached Letterboxd lists if the cache exists."""

    path = Path(cache_path).expanduser()
    if not path.exists():
        return []

    try:
        with path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return []

    if isinstance(raw, dict):
        raw = raw.get("lists", [])
    if not isinstance(raw, list):
        return []

    result: List[Mapping[str, object]] = []
    for entry in raw:
        if (
            isinstance(entry, Mapping)
            and entry.get("title")
            and entry.get("url_suffix")
        ):
            result.append(entry)
    return result


def save_lists(cache_path: str | Path, lists: Iterable[Mapping[str, object]]) -> None:
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
