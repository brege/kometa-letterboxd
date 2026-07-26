"""Generate Kometa collections for Letterboxd Showdowns."""

from __future__ import annotations

import datetime
from collections.abc import Iterable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import requests
import yaml

from kometa_letterboxd.common.config import ShowdownConfig, resolve_path
from kometa_letterboxd.common.kometa import build_collection_entry
from kometa_letterboxd.common.plex import (
    build_tmdb_library_index,
    connect_to_plex,
    count_available_tmdb_ids,
    resolve_plex_config,
)

from .probe import refresh_showdown_cache
from .storage import (
    load_showdown_datasets,
    load_state,
    save_state,
)

DEFAULT_STATE_FILE = Path("data/featured/showdown/rotation.json")
LifecycleState = Literal["spotlight", "library", "retire"]


def _resolve_required_path(raw_path: str | Path, base_path: Path) -> Path:
    resolved = resolve_path(raw_path, base_path)
    if resolved is None:
        raise ValueError(f"Unable to resolve path {raw_path}")
    return resolved


@dataclass
class ShowdownAvailability:
    slug: str
    title: str
    showdown_url: str
    total_entries: int
    available_entries: int
    published_at: str | None

    @property
    def match_ratio(self) -> float:
        if self.total_entries <= 0:
            return 0.0
        return self.available_entries / self.total_entries

    @property
    def published_datetime(self) -> datetime.datetime | None:
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
    _all_lists: Sequence[object],
    showdown_config: ShowdownConfig | None,
    *,
    base_path: Path,
    kometa_config_path: Path | None,
    config_source: Path,
) -> tuple[dict[str, MutableMapping[str, Any]], Path | None, list[str]]:
    if showdown_config is None:
        return {}, None, []

    showdown_path = _resolve_required_path(showdown_config.showdown_json, base_path)
    if not showdown_path.exists():
        print(f"Showdown dataset not found at {showdown_path}; skipping generation.")
        return {}, None, []

    datasets = load_showdown_datasets(showdown_path)
    if not datasets:
        print("Showdown dataset contained no entries; skipping generation.")
        return {}, None, []

    if kometa_config_path is None:
        raise ValueError("Showdown requires kometa.config_path")
    plex_config = resolve_plex_config(
        kometa_config_path,
        library_override=showdown_config.library,
    )
    plex_server = connect_to_plex(plex_config)
    library = plex_server.library.section(plex_config.library)
    tmdb_index = build_tmdb_library_index(library)

    availability = _evaluate_datasets(datasets, tmdb_index, showdown_config.threshold)
    if not availability:
        print("Showdown: no datasets met the threshold; nothing to add.")
        return {}, None, []

    ordered = _sort_availability(availability, showdown_config.sort)

    state_path = resolve_path(showdown_config.state_file, base_path)
    if not state_path:
        state_path = (base_path / DEFAULT_STATE_FILE).resolve()

    state = load_state(state_path)

    selected, spotlight, next_spotlight_position = _select_sliding_window_and_spotlight(
        ordered,
        showdown_config.window,
        state.window_position,
    )
    label = showdown_config.label

    slug_title_map = _build_slug_title_map(datasets)

    _update_collection_lifecycles(
        state.collection_lifecycles,
        ordered,
        selected,
        spotlight,
    )

    state.collection_titles.update(slug_title_map)
    state.window_position = next_spotlight_position

    retired_collection_names = _get_retired_collection_names(
        state.collection_lifecycles,
        state.collection_titles,
    )

    collections = _build_collections(
        selected,
        datasets,
        tmdb_index,
        spotlight,
        label,
        state.collection_lifecycles,
        retired_collection_names,
    )

    save_state(state_path, state)

    if showdown_config.asset_directory:
        asset_path = _resolve_required_path(
            showdown_config.asset_directory,
            base_path,
        )
        _download_background_images(collections, datasets, asset_path)

    destination_path = resolve_path(showdown_config.kometa_destination, base_path)

    return collections, destination_path, retired_collection_names


def _evaluate_datasets(
    datasets: Iterable[Mapping[str, Any]],
    tmdb_index: Iterable[str],
    threshold: int,
) -> list[ShowdownAvailability]:
    index_set = {str(tmdb_id) for tmdb_id in tmdb_index}
    availability: list[ShowdownAvailability] = []

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
) -> list[ShowdownAvailability]:
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
    current_spotlight_position: int,
) -> tuple[
    list[ShowdownAvailability],
    ShowdownAvailability | None,
    int,
]:
    if not ordered:
        return [], None, current_spotlight_position

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

    # Defer persisting the new spotlight position to the caller
    return selected, spotlight, next_spotlight_position


def _build_slug_title_map(datasets: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    mapping: dict[str, str] = {}
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
    collection_lifecycles: MutableMapping[str, LifecycleState],
    ordered: Sequence[ShowdownAvailability],
    selected: Sequence[ShowdownAvailability],
    spotlight: ShowdownAvailability | None,
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
            if current_state in (None, "spotlight", "retire"):
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
    collection_lifecycles: Mapping[str, LifecycleState],
    slug_to_title: Mapping[str, str] | None,
) -> list[str]:
    if not collection_lifecycles:
        return []

    titles = slug_to_title or {}
    retired: list[str] = []

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
    spotlight: ShowdownAvailability | None,
    label: str,
    collection_lifecycles: Mapping[str, LifecycleState],
    retired_names: Sequence[str],
) -> dict[str, MutableMapping[str, Any]]:
    collections: dict[str, MutableMapping[str, Any]] = {}
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
                percent = round((item.available_entries / item.total_entries) * 100)
            summary = (
                f"{item.available_entries}/{item.total_entries} titles owned "
                f"({percent}%)."
                if item.total_entries
                else "No titles available in Plex."
            )

        # Get the available TMDB IDs for this showdown
        available_tmdb_ids = slug_to_tmdb_ids.get(item.slug, [])

        # Build extra dict with label
        extra_dict: dict[str, object] = {"label": label}
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
    collections: dict[str, MutableMapping[str, Any]],
    datasets: Iterable[Mapping[str, Any]],
    asset_directory: Path,
) -> None:
    """Download background images for collections to asset directory."""

    # Create asset directory if it doesn't exist
    asset_directory.mkdir(parents=True, exist_ok=True)

    # Create asset directory if needed for background images

    for collection_name in collections:
        # Find the dataset for this collection by matching titles
        dataset = None
        for item in datasets:
            if (
                isinstance(item, Mapping)
                and isinstance(item.get("summary"), Mapping)
                and item["summary"].get("title") == collection_name
            ):
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

        except (OSError, requests.RequestException) as exc:
            print(f"  ! Failed to download background for '{collection_name}': {exc}")


def _write_manifest(
    path: Path,
    collections: Mapping[str, Mapping[str, Any]],
    *,
    label: str,
    spotlight: ShowdownAvailability | None,
    config_source: Path,
    window_size: int,
    retired_collections: Sequence[str] | None = None,
) -> None:
    manifest_data: dict[str, object] = {
        "collections": {name: dict(value) for name, value in collections.items()},
    }

    if retired_collections:
        manifest_data["delete_collections_named"] = list(retired_collections)

    path.parent.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")

    header_lines = [
        "# Managed by collectors.featured.showdown",
        f"# Generated on {generated_at}",
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
