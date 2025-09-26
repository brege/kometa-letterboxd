"""Cache helpers for generic Letterboxd list fetches."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, List, Mapping


def load_lists(cache_path: Path) -> List[Mapping[str, object]]:
    if not cache_path.exists():
        return []
    try:
        with cache_path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return []

    if isinstance(raw, dict):
        raw = raw.get("lists", [])
    if not isinstance(raw, list):
        return []
    result: List[Mapping[str, object]] = []
    for entry in raw:
        if isinstance(entry, dict) and entry.get("title") and entry.get("url_suffix"):
            result.append(entry)
    return result


def save_lists(cache_path: Path, lists: Iterable[Mapping[str, object]]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
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
    with cache_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
