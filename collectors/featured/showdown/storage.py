"""File helpers for the showdown collector."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence


def resolve_path(raw: Any, base_path: Path) -> Optional[Path]:
    """Resolve a showdown-relative path against the collector base path."""

    if not raw:
        return None
    candidate = Path(str(raw)).expanduser()
    if not candidate.is_absolute():
        candidate = (base_path / candidate).resolve()
    return candidate


def load_showdown_datasets(path: Path) -> List[Mapping[str, Any]]:
    """Load showdown datasets from the cached JSON payload."""

    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Showdown: unable to read dataset {path}: {exc}")
        return []

    if isinstance(payload, dict) and "showdowns" in payload:
        payload = payload.get("showdowns")

    if not isinstance(payload, Sequence):
        print("Showdown: unexpected dataset structure; expected a list of items.")
        return []

    datasets: List[Mapping[str, Any]] = []
    for item in payload:
        if isinstance(item, Mapping):
            datasets.append(item)
    return datasets


def load_showdown_cache(path: Path) -> Dict[str, Dict[str, Any]]:
    """Load showdown datasets keyed by slug for cache reuse."""

    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}

    entries: Sequence[Mapping[str, Any]]
    if isinstance(payload, dict) and "showdowns" in payload:
        raw_entries = payload.get("showdowns")
        entries = raw_entries if isinstance(raw_entries, Sequence) else []
    elif isinstance(payload, Sequence):
        entries = payload
    else:
        return {}

    cache: Dict[str, Dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        summary = entry.get("summary") if isinstance(entry, Mapping) else None
        if not isinstance(summary, Mapping):
            continue
        slug = summary.get("slug")
        if not slug:
            continue
        cache[str(slug)] = dict(entry)
    return cache


def save_showdown_cache(path: Path, cache: Mapping[str, Mapping[str, Any]]) -> None:
    """Persist showdown cache in the expected JSON structure."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"showdowns": [dict(value) for value in cache.values()]}
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=False)


def load_state(path: Path) -> Dict[str, Any]:
    """Load showdown rotation state from disk."""

    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def save_state(path: Path, data: Mapping[str, Any]) -> None:
    """Persist showdown rotation state to disk."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(
            dict(data),
            handle,
            indent=2,
            sort_keys=True,
        )
