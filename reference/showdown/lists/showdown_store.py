"""Cache helpers for Letterboxd showdown datasets."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict


CACHE_KEY = "showdowns"


def load_cache(cache_path: Path) -> Dict[str, Dict[str, object]]:
    if not cache_path.exists():
        return {}
    try:
        with cache_path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}

    datasets = []
    if isinstance(raw, dict) and CACHE_KEY in raw:
        datasets = raw.get(CACHE_KEY, []) or []
    elif isinstance(raw, list):
        datasets = raw

    cache: Dict[str, Dict[str, object]] = {}
    for entry in datasets:
        if not isinstance(entry, dict):
            continue
        summary = entry.get("summary", {})
        if not isinstance(summary, dict):
            continue
        slug = summary.get("slug")
        if not slug:
            continue
        cache[str(slug)] = entry
    return cache


def save_cache(cache: Dict[str, Dict[str, object]], cache_path: Path) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {CACHE_KEY: list(cache.values())}
    with cache_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=False)
