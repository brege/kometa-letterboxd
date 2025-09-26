"""Generate Kometa collections for Letterboxd Showdowns."""

from __future__ import annotations

import datetime
import json
from dataclasses import dataclass
from pathlib import Path
from typing import (
    Any,
    Dict,
    Iterable,
    List,
    Mapping,
    MutableMapping,
    Optional,
    Sequence,
    Tuple,
)

import yaml

from common.kometa import build_collection_entry
from common.plex import (
    build_tmdb_library_index,
    connect_to_plex,
    count_available_tmdb_ids,
    resolve_plex_config,
)

DEFAULT_THRESHOLD = 4
DEFAULT_SORT_MODE = "matches_desc"
DEFAULT_WINDOW = 5
DEFAULT_LABEL = "Showdown Spotlight"
DEFAULT_STATE_FILE = Path("data/showdown_state.json")


@dataclass
class ShowdownAvailability:
    slug: str
    title: str
    showdown_url: str
    total_entries: int
    available_entries: int
    published_at: Optional[str]

    @property
    def match_ratio(self) -> float:
        if self.total_entries <= 0:
            return 0.0
        return self.available_entries / self.total_entries

    @property
    def published_datetime(self) -> Optional[datetime.datetime]:
        if not self.published_at:
            return None
        value = self.published_at
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        try:
            return datetime.datetime.fromisoformat(value)
        except ValueError:
            return None


def generate_showdown_collections(
    _all_lists: Sequence[Sequence[str]],
    showdown_config: Mapping[str, Any],
    *,
    base_path: Path,
    kometa_config_path: Path | None,
    config_source: Path,
) -> Tuple[Dict[str, MutableMapping[str, Any]], Optional[Path]]:
    if not showdown_config:
        return {}, None

    showdown_path = _resolve_path(showdown_config.get("showdown_json"), base_path)
    if not showdown_path:
        print("Showdown: no dataset path configured; skipping showdown collections.")
        return {}, None
    if not showdown_path.exists():
        print(f"Showdown dataset not found at {showdown_path}; skipping generation.")
        return {}, None

    datasets = _load_showdown_datasets(showdown_path)
    if not datasets:
        print("Showdown dataset contained no entries; skipping generation.")
        return {}, None

    if kometa_config_path is None:
        print(
            "Showdown: no Kometa config path provided; skipping showdown collections."
        )
        return {}, None

    try:
        plex_config = resolve_plex_config(
            kometa_config_path,
            library_override=showdown_config.get("library"),
        )
        plex_server = connect_to_plex(plex_config)
        library = plex_server.library.section(plex_config.library)
        tmdb_index = build_tmdb_library_index(library)
    except Exception as exc:  # pragma: no cover - relies on Plex environment
        print(f"Showdown: unable to evaluate Plex library ({exc}); skipping.")
        return {}, None

    threshold = int(showdown_config.get("threshold", DEFAULT_THRESHOLD))
    availability = _evaluate_datasets(datasets, tmdb_index, threshold)
    if not availability:
        print("Showdown: no datasets met the threshold; nothing to add.")
        return {}, None

    sort_mode = str(showdown_config.get("sort", DEFAULT_SORT_MODE))
    ordered = _sort_availability(availability, sort_mode)

    window = int(showdown_config.get("window", DEFAULT_WINDOW))
    if window <= 0:
        print("Showdown: window size must be positive; skipping generation.")
        return {}, None
    selected = ordered[:window]
    if not selected:
        print("Showdown: no datasets selected after applying window.")
        return {}, None

    state_path = _resolve_path(showdown_config.get("state_file"), base_path)
    if not state_path:
        state_path = (base_path / DEFAULT_STATE_FILE).resolve()

    spotlight = _select_spotlight(selected, state_path)
    label = str(showdown_config.get("label", DEFAULT_LABEL))

    collections = _build_collections(selected, spotlight, label)

    destination_path = _resolve_path(
        showdown_config.get("kometa_destination"), base_path
    )

    manifest_path = _resolve_path(showdown_config.get("manifest_output"), base_path)
    if manifest_path:
        _write_manifest(
            manifest_path,
            collections,
            label=label,
            spotlight=spotlight,
            config_source=config_source,
            window_size=len(selected),
        )

    return collections, destination_path


def _resolve_path(raw: Any, base_path: Path) -> Optional[Path]:
    if not raw:
        return None
    candidate = Path(str(raw)).expanduser()
    if not candidate.is_absolute():
        candidate = (base_path / candidate).resolve()
    return candidate


def _load_showdown_datasets(path: Path) -> List[Mapping[str, Any]]:
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


def _evaluate_datasets(
    datasets: Iterable[Mapping[str, Any]],
    tmdb_index: Iterable[str],
    threshold: int,
) -> List[ShowdownAvailability]:
    index_set = {str(tmdb_id) for tmdb_id in tmdb_index}
    availability: List[ShowdownAvailability] = []

    for item in datasets:
        summary = item.get("summary") if isinstance(item, Mapping) else None
        if not isinstance(summary, Mapping):
            continue

        slug = str(summary.get("slug", "")).strip()
        title = str(summary.get("title", slug)).strip() or slug
        showdown_url = str(summary.get("showdown_url", "")).strip()
        entries = item.get("entries") if isinstance(item, Mapping) else None
        if not isinstance(entries, Sequence):
            continue

        tmdb_ids = [
            str(entry.get("tmdb_id"))
            for entry in entries
            if isinstance(entry, Mapping) and entry.get("tmdb_id")
        ]
        available = count_available_tmdb_ids(tmdb_ids, index_set)

        total_entries = len([entry for entry in entries if isinstance(entry, Mapping)])
        published_at = item.get("published_at") if isinstance(item, Mapping) else None

        if available < threshold:
            continue

        availability.append(
            ShowdownAvailability(
                slug=slug,
                title=title,
                showdown_url=showdown_url,
                total_entries=total_entries,
                available_entries=available,
                published_at=published_at if isinstance(published_at, str) else None,
            )
        )

    return availability


def _sort_availability(
    items: Sequence[ShowdownAvailability],
    sort_mode: str,
) -> List[ShowdownAvailability]:
    if sort_mode == "matches_asc":
        return sorted(items, key=_availability_sort_key)
    if sort_mode == "none":
        return list(items)
    return sorted(items, key=_availability_sort_key, reverse=True)


def _availability_sort_key(item: ShowdownAvailability) -> Any:
    published = item.published_datetime
    if published is None:
        published = datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)
    return (item.match_ratio, item.available_entries, published, item.title)


def _select_spotlight(
    ordered: Sequence[ShowdownAvailability],
    state_path: Path,
) -> Optional[ShowdownAvailability]:
    if not ordered:
        return None

    state = _load_state(state_path)
    seen = [slug for slug in state.get("seen", []) if slug]
    pool = {item.slug for item in ordered}
    seen = [slug for slug in seen if slug in pool]

    spotlight: Optional[ShowdownAvailability] = None
    for item in ordered:
        if item.slug not in seen:
            spotlight = item
            break

    if spotlight is None:
        spotlight = ordered[0]
        seen = []

    if spotlight.slug in seen:
        seen.remove(spotlight.slug)
    seen.append(spotlight.slug)

    state["seen"] = seen[-20:]
    _save_state(state_path, state)
    return spotlight


def _load_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"seen": []}
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {"seen": []}
    if not isinstance(data, dict):
        return {"seen": []}
    seen = data.get("seen", [])
    if not isinstance(seen, list):
        seen = []
    filtered = [slug for slug in seen if isinstance(slug, str) and slug]
    return {"seen": filtered}


def _save_state(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(
            {"seen": list(data.get("seen", []))}, handle, indent=2, sort_keys=True
        )


def _build_collections(
    availability: Sequence[ShowdownAvailability],
    spotlight: Optional[ShowdownAvailability],
    label: str,
) -> Dict[str, MutableMapping[str, Any]]:
    collections: Dict[str, MutableMapping[str, Any]] = {}
    spotlight_slug = spotlight.slug if spotlight else None

    for index, item in enumerate(availability):
        percent = 0
        if item.total_entries > 0:
            percent = int(round((item.available_entries / item.total_entries) * 100))

        summary = (
            f"{item.available_entries}/{item.total_entries} titles owned ({percent}%)."
            if item.total_entries
            else "No titles available in Plex."
        )

        collection = build_collection_entry(
            item.showdown_url or f"https://letterboxd.com/showdown/{item.slug}/",
            sort_title=(
                f"Showdown {index:02d} {item.available_entries:02d}/{item.total_entries:02d}"
                f" {item.title}"
            ),
            collection_order="custom",
            summary=summary,
            visible_library=True,
            visible_home=item.slug == spotlight_slug,
            visible_shared=item.slug == spotlight_slug,
            extra={"label": label},
        )

        collections[item.title] = collection

    return collections


def _write_manifest(
    path: Path,
    collections: Mapping[str, Mapping[str, Any]],
    *,
    label: str,
    spotlight: Optional[ShowdownAvailability],
    config_source: Path,
    window_size: int,
) -> None:
    manifest_data = {
        "collections": {name: dict(value) for name, value in collections.items()},
    }

    path.parent.mkdir(parents=True, exist_ok=True)

    header_lines = [
        "# Managed by collectors.featured.showdown",
        f"# Generated on {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"# Source config: {config_source}",
        f"# Spotlight: {spotlight.title if spotlight else 'n/a'}",
        f"# Window size: {window_size} (label: {label})",
        "",
    ]

    with path.open("w", encoding="utf-8") as handle:
        for line in header_lines:
            handle.write(f"{line}\n")
        yaml.safe_dump(
            manifest_data,
            handle,
            sort_keys=False,
            allow_unicode=False,
        )
