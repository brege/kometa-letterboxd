"""Generate Kometa collections for Letterboxd Showdowns."""

from __future__ import annotations

import datetime
import requests
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
from .probe import refresh_showdown_cache
from .storage import load_showdown_datasets, load_state, resolve_path, save_state

DEFAULT_THRESHOLD = 4
DEFAULT_SORT_MODE = "matches_desc"
DEFAULT_WINDOW = 5
DEFAULT_LABEL = "Showdown Spotlight"
DEFAULT_STATE_FILE = Path("data/featured/showdown/rotation.json")


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
) -> Tuple[Dict[str, MutableMapping[str, Any]], Optional[Path], List[str]]:
    if not showdown_config:
        return {}, None, []

    showdown_path = resolve_path(showdown_config.get("showdown_json"), base_path)
    if not showdown_path:
        print("Showdown: no dataset path configured; skipping showdown collections.")
        return {}, None, []
    if not showdown_path.exists():
        print(f"Showdown dataset not found at {showdown_path}; skipping generation.")
        return {}, None, []

    datasets = load_showdown_datasets(showdown_path)
    if not datasets:
        print("Showdown dataset contained no entries; skipping generation.")
        return {}, None, []

    if kometa_config_path is None:
        print(
            "Showdown: no Kometa config path provided; skipping showdown collections."
        )
        return {}, None, []

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
        return {}, None, []

    threshold = int(showdown_config.get("threshold", DEFAULT_THRESHOLD))
    availability = _evaluate_datasets(datasets, tmdb_index, threshold)
    if not availability:
        print("Showdown: no datasets met the threshold; nothing to add.")
        return {}, None, []

    sort_mode = str(showdown_config.get("sort", DEFAULT_SORT_MODE))
    ordered = _sort_availability(availability, sort_mode)

    window = int(showdown_config.get("window", DEFAULT_WINDOW))
    if window <= 0:
        print("Showdown: window size must be positive; skipping generation.")
        return {}, None, []

    state_path = resolve_path(showdown_config.get("state_file"), base_path)
    if not state_path:
        state_path = (base_path / DEFAULT_STATE_FILE).resolve()

    # Calculate sliding window based on spotlight progression
    selected, spotlight = _select_sliding_window_and_spotlight(
        ordered, window, state_path
    )
    label = str(showdown_config.get("label", DEFAULT_LABEL))

    state = load_state(state_path)
    lifecycle_state = state.get("collection_lifecycles")
    if not isinstance(lifecycle_state, dict):
        lifecycle_state = {}
    else:
        lifecycle_state = {
            str(slug): str(status) for slug, status in lifecycle_state.items()
        }

    slug_title_map = _build_slug_title_map(datasets)

    _update_collection_lifecycles(
        lifecycle_state,
        ordered,
        selected,
        spotlight,
    )

    stored_titles = state.get("collection_titles")
    if not isinstance(stored_titles, dict):
        stored_titles = {}
    stored_titles.update(slug_title_map)

    state["collection_lifecycles"] = lifecycle_state
    state["collection_titles"] = dict(stored_titles)

    retired_collection_names = _get_retired_collection_names(
        lifecycle_state,
        state.get("collection_titles"),
    )

    collections = _build_collections(
        selected,
        datasets,
        tmdb_index,
        spotlight,
        label,
        lifecycle_state,
        retired_collection_names,
    )

    save_state(state_path, state)

    # Download background images to asset directory if configured
    asset_directory_config = showdown_config.get("asset_directory")
    if asset_directory_config:
        asset_path = resolve_path(asset_directory_config, base_path)
        if asset_path:
            _download_background_images(collections, datasets, asset_path)

    destination_path = resolve_path(
        showdown_config.get("kometa_destination"), base_path
    )

    return collections, destination_path, retired_collection_names


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


def _select_sliding_window_and_spotlight(
    ordered: Sequence[ShowdownAvailability],
    window: int,
    state_path: Path,
) -> Tuple[List[ShowdownAvailability], Optional[ShowdownAvailability]]:
    if not ordered:
        return [], None

    if window <= 0:
        return [], None

    # Load state to get current spotlight position (instead of window position)
    state = load_state(state_path)
    current_spotlight_position = state.get("window_position", 0)

    # Ensure spotlight position is valid
    if current_spotlight_position < 0 or current_spotlight_position >= len(ordered):
        current_spotlight_position = 0

    # Calculate window bounds centered around spotlight position
    # Spotlight should be at index 2 (position 3) within the window
    spotlight_offset = min(2, window // 2)
    window_start = max(0, current_spotlight_position - spotlight_offset)
    window_end = min(window_start + window, len(ordered))

    # Adjust window_start if we hit the end boundary
    if window_end - window_start < window and window_start > 0:
        window_start = max(0, window_end - window)

    # Extract window of collections
    selected = list(ordered[window_start:window_end])

    # Find spotlight within the selected window
    spotlight_index_in_window = current_spotlight_position - window_start
    spotlight = (
        selected[spotlight_index_in_window]
        if spotlight_index_in_window < len(selected)
        else None
    )

    # Advance spotlight position for next run (daily rotation)
    next_spotlight_position = current_spotlight_position + 1
    if next_spotlight_position >= len(ordered):
        # Reset to beginning when we've gone through all collections
        next_spotlight_position = 0

    # Save new spotlight position
    state["window_position"] = next_spotlight_position
    save_state(state_path, state)

    return selected, spotlight


def _build_slug_title_map(datasets: Iterable[Mapping[str, Any]]) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for item in datasets:
        summary = item.get("summary") if isinstance(item, Mapping) else None
        if not isinstance(summary, Mapping):
            continue
        slug = str(summary.get("slug", "")).strip()
        if not slug:
            continue
        title = str(summary.get("title", slug)).strip() or slug
        mapping[slug] = title
    return mapping


def _update_collection_lifecycles(
    collection_lifecycles: MutableMapping[str, str],
    ordered: Sequence[ShowdownAvailability],
    selected: Sequence[ShowdownAvailability],
    spotlight: Optional[ShowdownAvailability],
) -> None:
    selected_slugs = {item.slug for item in selected}
    spotlight_slug = spotlight.slug if spotlight else None
    ordered_slugs = [item.slug for item in ordered]

    for slug in ordered_slugs:
        if slug == spotlight_slug:
            collection_lifecycles[slug] = "spotlight"
            continue

        current_state = collection_lifecycles.get(slug)
        if slug in selected_slugs:
            if current_state == "spotlight":
                collection_lifecycles[slug] = "library"
            elif current_state is None or current_state == "retire":
                collection_lifecycles[slug] = "library"
            # existing "library" state remains unchanged
        else:
            if current_state in {"spotlight", "library"}:
                collection_lifecycles[slug] = "retire"

    # Any slug that disappeared from the ordered list should also retire
    known_slugs = set(collection_lifecycles.keys())
    missing_slugs = known_slugs - set(ordered_slugs)
    for slug in missing_slugs:
        collection_lifecycles[slug] = "retire"


def _get_retired_collection_names(
    collection_lifecycles: Mapping[str, str],
    slug_to_title: Mapping[str, str] | None,
) -> List[str]:
    if not collection_lifecycles:
        return []

    titles = slug_to_title or {}
    retired: List[str] = []

    for slug, title in titles.items():
        if collection_lifecycles.get(slug) == "retire" and title:
            retired.append(title)

    # Include any retired slugs missing from the title map using slug as fallback
    for slug, state in collection_lifecycles.items():
        if state == "retire" and slug not in titles and slug:
            retired.append(slug)

    return retired


def _build_collections(
    availability: Sequence[ShowdownAvailability],
    datasets: Iterable[Mapping[str, Any]],
    tmdb_index: Iterable[str],
    spotlight: Optional[ShowdownAvailability],
    label: str,
    collection_lifecycles: Mapping[str, str],
    retired_names: Sequence[str],
) -> Dict[str, MutableMapping[str, Any]]:
    collections: Dict[str, MutableMapping[str, Any]] = {}
    spotlight_slug = spotlight.slug if spotlight else None

    # Create a mapping from slug to available TMDB IDs
    index_set = {str(tmdb_id) for tmdb_id in tmdb_index}
    slug_to_tmdb_ids = {}

    for item in datasets:
        summary = item.get("summary") if isinstance(item, Mapping) else None
        if not isinstance(summary, Mapping):
            continue

        slug = str(summary.get("slug", "")).strip()
        entries = item.get("entries") if isinstance(item, Mapping) else None
        if not isinstance(entries, Sequence):
            continue

        # Get all TMDB IDs for this showdown
        all_tmdb_ids = [
            str(entry.get("tmdb_id"))
            for entry in entries
            if isinstance(entry, Mapping) and entry.get("tmdb_id")
        ]

        # Filter to only available TMDB IDs (those in Plex library)
        available_tmdb_ids = [
            tmdb_id for tmdb_id in all_tmdb_ids if tmdb_id in index_set
        ]
        slug_to_tmdb_ids[slug] = available_tmdb_ids

    # Create a mapping from slug to dataset for description lookup
    slug_to_dataset = {
        str(item.get("summary", {}).get("slug", "")): item
        for item in datasets
        if isinstance(item, Mapping) and isinstance(item.get("summary"), Mapping)
    }

    for index, item in enumerate(availability):
        # Try to get the full description from the dataset
        dataset = slug_to_dataset.get(item.slug)
        description = None
        if dataset and isinstance(dataset.get("summary"), Mapping):
            description = dataset["summary"].get("description")

        if description:
            # Use the full description with the showdown URL
            summary = f"{description.strip()}\n\n{item.showdown_url}"
        else:
            # Fallback to percentage summary
            percent = 0
            if item.total_entries > 0:
                percent = int(
                    round((item.available_entries / item.total_entries) * 100)
                )
            summary = (
                f"{item.available_entries}/{item.total_entries} titles owned "
                f"({percent}%)."
                if item.total_entries
                else "No titles available in Plex."
            )

        # Get the available TMDB IDs for this showdown
        available_tmdb_ids = slug_to_tmdb_ids.get(item.slug, [])

        # Build extra dict with label
        extra_dict = {"label": label}
        if index == 0 and retired_names:
            extra_dict["delete_collections_named"] = list(dict.fromkeys(retired_names))
        # Note: background images are handled via asset directories, not YAML fields

        lifecycle_state = collection_lifecycles.get(item.slug, "library")
        if lifecycle_state == "spotlight":
            visible_library = True
            visible_home = True
            visible_shared = True
        elif lifecycle_state == "library":
            visible_library = True
            visible_home = False
            visible_shared = False
        else:
            # Fallback for unexpected states
            visible_library = True
            visible_home = item.slug == spotlight_slug
            visible_shared = item.slug == spotlight_slug

        collection = build_collection_entry(
            item.showdown_url or f"https://letterboxd.com/showdown/{item.slug}/",
            sort_title=(
                f"+4 Showdown {index:02d} "
                f"{item.available_entries:02d}/{item.total_entries:02d} {item.title}"
            ),
            collection_order=None,
            summary=summary,
            visible_library=visible_library,
            visible_home=visible_home,
            visible_shared=visible_shared,
            extra=extra_dict,
            tmdb_ids=available_tmdb_ids,
        )

        collections[item.title] = collection

    return collections


def _download_background_images(
    collections: Dict[str, MutableMapping[str, Any]],
    datasets: Iterable[Mapping[str, Any]],
    asset_directory: Path,
) -> None:
    """Download background images for collections to asset directory."""

    # Create asset directory if it doesn't exist
    asset_directory.mkdir(parents=True, exist_ok=True)

    # Create asset directory if needed for background images

    for collection_name in collections.keys():
        # Find the dataset for this collection by matching titles
        dataset = None
        for item in datasets:
            if isinstance(item, Mapping) and isinstance(item.get("summary"), Mapping):
                if item["summary"].get("title") == collection_name:
                    dataset = item
                    break

        if not dataset:
            continue

        background_url = dataset.get("summary", {}).get("background_image")
        if not background_url:
            continue

        try:
            # Create collection asset directory
            collection_dir = asset_directory / collection_name
            collection_dir.mkdir(parents=True, exist_ok=True)

            # Download the background image
            print(f"Downloading background image for '{collection_name}'...")
            response = requests.get(background_url, timeout=30)
            response.raise_for_status()

            # Determine file extension from URL
            if background_url.endswith(".jpg"):
                ext = ".jpg"
            elif background_url.endswith(".png"):
                ext = ".png"
            elif background_url.endswith(".webp"):
                ext = ".webp"
            else:
                ext = ".jpg"  # Default to jpg

            # Save the image as background.ext in the collection directory
            background_path = collection_dir / f"background{ext}"
            background_path.write_bytes(response.content)

            print(f"  → Saved to {background_path}")

        except Exception as e:
            print(f"  ! Failed to download background for '{collection_name}': {e}")


def _write_manifest(
    path: Path,
    collections: Mapping[str, Mapping[str, Any]],
    *,
    label: str,
    spotlight: Optional[ShowdownAvailability],
    config_source: Path,
    window_size: int,
    retired_collections: Sequence[str] | None = None,
) -> None:
    manifest_data = {
        "collections": {name: dict(value) for name, value in collections.items()},
    }

    if retired_collections:
        manifest_data["delete_collections_named"] = list(retired_collections)

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


__all__ = ["generate_showdown_collections", "refresh_showdown_cache"]
