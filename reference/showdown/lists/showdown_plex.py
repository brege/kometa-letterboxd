"""Utilities for matching Showdown datasets against Plex libraries."""

from __future__ import annotations

import argparse
import datetime
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, TYPE_CHECKING

import yaml

from .common.plex import (
    PlexConfig,
    build_tmdb_library_index,
    connect_to_plex,
    count_available_tmdb_ids,
    load_letterboxd_config,
    resolve_plex_config,
)
from .showdown import ShowdownDataset, ShowdownEntry
from .utils import resolve_path

if TYPE_CHECKING:  # pragma: no cover
    from plexapi.server import PlexServer


@dataclass
class ShowdownAvailability:
    slug: str
    title: str
    total_entries: int
    available_entries: int
    threshold_met: bool
    missing_entries: List[ShowdownEntry]
    letterboxd_url: str
    published_at: Optional[str]

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

    @property
    def match_ratio(self) -> float:
        if self.total_entries <= 0:
            return 0.0
        return self.available_entries / self.total_entries


def load_showdown_file(path: str | Path) -> List[ShowdownDataset]:
    """Load showdown datasets from a JSON file produced by the probe."""
    with Path(path).open("r", encoding="utf-8") as handle:
        raw = json.load(handle)

    datasets: List[ShowdownDataset] = []
    if isinstance(raw, dict) and "showdowns" in raw:
        raw = raw.get("showdowns", [])

    if not isinstance(raw, Sequence):
        raise ValueError("Unexpected showdown JSON structure")

    for entry in raw:
        if not isinstance(entry, dict):
            continue
        datasets.append(ShowdownDataset.from_dict(entry))
    return datasets


def _filter_entries_with_tmdb(entries: Iterable[ShowdownEntry]) -> List[ShowdownEntry]:
    return [entry for entry in entries if entry.tmdb_id]


def evaluate_showdowns_against_plex(
    datasets: Sequence[ShowdownDataset],
    plex_server: "PlexServer",
    library_name: str,
    threshold: int = 4,
) -> List[ShowdownAvailability]:
    library = plex_server.library.section(library_name)
    tmdb_index = build_tmdb_library_index(library)

    availability: List[ShowdownAvailability] = []
    for dataset in datasets:
        entries_with_ids = _filter_entries_with_tmdb(dataset.entries)
        available_count = count_available_tmdb_ids(
            (entry.tmdb_id for entry in entries_with_ids), tmdb_index
        )
        missing_entries = [
            entry for entry in entries_with_ids if entry.tmdb_id not in tmdb_index
        ]
        availability.append(
            ShowdownAvailability(
                slug=dataset.summary.slug,
                title=dataset.summary.title,
                total_entries=dataset.entry_count,
                available_entries=available_count,
                threshold_met=available_count >= threshold,
                missing_entries=missing_entries,
                letterboxd_url=dataset.summary.showdown_url,
                published_at=dataset.published_at,
            )
        )
    return availability


def load_plex_and_evaluate(
    showdown_json_path: str | Path,
    letterboxd_config_path: str | Path,
    threshold: int = 4,
    library_override: Optional[str] = None,
) -> List[ShowdownAvailability]:
    datasets = load_showdown_file(showdown_json_path)
    config_path = Path(letterboxd_config_path).expanduser()
    lb_config = load_letterboxd_config(config_path)

    kometa_block = lb_config.get("kometa") if isinstance(lb_config, dict) else None
    if not isinstance(kometa_block, dict):
        raise ValueError("Letterboxd configuration must define a 'kometa.config_path'")

    raw_path = kometa_block.get("config_path")
    if not raw_path:
        raise ValueError("Letterboxd configuration missing 'kometa.config_path'")

    kometa_path = Path(raw_path).expanduser()
    if not kometa_path.is_absolute():
        kometa_path = (config_path.parent / kometa_path).resolve()

    plex_config: PlexConfig = resolve_plex_config(
        kometa_path,
        library_override=library_override,
    )
    server = connect_to_plex(plex_config)
    return evaluate_showdowns_against_plex(
        datasets,
        plex_server=server,
        library_name=plex_config.library,
        threshold=threshold,
    )


def run_showdown_job(
    *,
    letterboxd_config_path: Path,
    showdown_json_path: Path,
    threshold: int,
    sort_mode: str,
    manifest_output: Optional[Path],
    window: int,
    label: str,
    state_file: Optional[Path] = None,
    library_override: Optional[str] = None,
) -> None:
    results = load_plex_and_evaluate(
        showdown_json_path=showdown_json_path,
        letterboxd_config_path=letterboxd_config_path,
        threshold=threshold,
        library_override=library_override,
    )

    ordered_results = sort_availability(results, sort_mode)
    _print_report(ordered_results)

    if not manifest_output:
        return

    eligible = [item for item in ordered_results if item.threshold_met]
    if not eligible:
        print("No showdown lists meet the threshold; manifest not written.")
        return

    window_size = max(1, window)
    if window_size % 2 == 0:
        window_size += 1

    state_path = state_file if state_file else STATE_PATH_DEFAULT
    spotlight = _select_spotlight(eligible, state_path)
    if spotlight is None:
        print("Unable to select a spotlight showdown; manifest not written.")
        return

    window_availability = _window_selection(eligible, spotlight, window_size)
    collections = _build_manifest_entries(
        window_availability,
        spotlight.slug,
        label,
    )

    _write_manifest(
        output_path=manifest_output,
        collections=collections,
        config_source=letterboxd_config_path,
        spotlight=spotlight,
        window_size=len(window_availability),
        label=label,
    )
    print(
        f"Spotlight manifest written to {manifest_output} "
        f"(spotlight: {spotlight.title})"
    )


def run_showdown_from_config(
    showdown_config: Dict[str, Any],
    *,
    letterboxd_config_path: Path,
) -> None:
    if not isinstance(showdown_config, dict):
        raise ValueError("Showdown configuration must be a mapping")

    base_dir = letterboxd_config_path.parent

    showdown_json_raw = showdown_config.get("showdown_json")
    if not showdown_json_raw:
        raise ValueError("'showdown.showdown_json' must be set in the configuration")
    showdown_json_path = resolve_path(showdown_json_raw, base_dir)

    manifest_raw = showdown_config.get("manifest_output")
    manifest_output = resolve_path(manifest_raw, base_dir) if manifest_raw else None

    state_raw = showdown_config.get("state_file")
    state_path = resolve_path(state_raw, base_dir) if state_raw else None

    threshold = int(showdown_config.get("threshold", 4))
    sort_mode = str(showdown_config.get("sort", "matches_desc"))
    window = int(showdown_config.get("window", DEFAULT_WINDOW))
    label = str(showdown_config.get("label", DEFAULT_LABEL))
    library_override = showdown_config.get("library")

    run_showdown_job(
        letterboxd_config_path=letterboxd_config_path,
        showdown_json_path=showdown_json_path,
        threshold=threshold,
        sort_mode=sort_mode,
        manifest_output=manifest_output,
        window=window,
        label=label,
        state_file=state_path,
        library_override=library_override,
    )


def sort_availability(
    results: Sequence[ShowdownAvailability],
    sort_mode: str,
) -> List[ShowdownAvailability]:
    def _sort_key(item: ShowdownAvailability):
        published = item.published_datetime
        if published is None:
            published = datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)
        return (
            item.match_ratio,
            item.available_entries,
            published,
            item.title,
        )

    if sort_mode == "matches_desc":
        return sorted(
            results,
            key=_sort_key,
            reverse=True,
        )
    if sort_mode == "matches_asc":
        return sorted(
            results,
            key=_sort_key,
        )
    return list(results)


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate Letterboxd showdown datasets against a Plex library"
    )
    parser.add_argument(
        "--showdown-json",
        required=True,
        help="Path to JSON produced by lists/showdown.py --json --output",
    )
    parser.add_argument(
        "--config",
        required=True,
        help="Path to the kometa-letterboxd configuration file",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=4,
        help="Minimum number of owned titles required to include the showdown",
    )
    parser.add_argument(
        "--library",
        help="Override Plex library name (defaults to config value)",
    )
    parser.add_argument(
        "--sort",
        choices=("matches_desc", "matches_asc", "none"),
        default="matches_desc",
        help="Sorting for the output and manifest generation",
    )
    parser.add_argument(
        "--manifest-output",
        help="Optional path to write a Kometa-ready spotlight manifest",
    )
    parser.add_argument(
        "--window",
        type=int,
        default=DEFAULT_WINDOW,
        help="Number of showdown collections to maintain (spotlight window)",
    )
    parser.add_argument(
        "--state-file",
        default=str(STATE_PATH_DEFAULT),
        help="Path to store spotlight rotation state",
    )
    parser.add_argument(
        "--label",
        default=DEFAULT_LABEL,
        help="Label applied to spotlight collections for easy cleanup",
    )
    return parser


def _print_report(results: Sequence[ShowdownAvailability]) -> None:
    for item in results:
        status = "OK" if item.threshold_met else "MISSING"
        try:
            print(
                f"{item.title} [{item.available_entries}/{item.total_entries}] → {status}"
            )
            if not item.threshold_met and item.missing_entries:
                for entry in item.missing_entries:
                    tmdb_id = entry.tmdb_id or "unknown"
                    print(f"  - {entry.rank:>2}. {entry.film_name} (tmdb:{tmdb_id})")
        except BrokenPipeError:
            return


STATE_PATH_DEFAULT = Path("data/showdown_state.json")
DEFAULT_LABEL = "Showdown Spotlight"
DEFAULT_WINDOW = 5


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
    data["seen"] = [slug for slug in seen if isinstance(slug, str)]
    return data


def _save_state(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)


def _select_spotlight(
    ordered_availability: Sequence[ShowdownAvailability],
    state_path: Path,
) -> Optional[ShowdownAvailability]:
    if not ordered_availability:
        return None

    state = _load_state(state_path)
    avail_slugs = [item.slug for item in ordered_availability]
    seen = [slug for slug in state.get("seen", []) if slug in avail_slugs]

    spotlight: Optional[ShowdownAvailability] = None
    for item in ordered_availability:
        if item.slug not in seen:
            spotlight = item
            break

    if spotlight is None:
        spotlight = ordered_availability[0]
        seen = []

    # Maintain seen list as recency queue
    if spotlight.slug in seen:
        seen.remove(spotlight.slug)
    seen.append(spotlight.slug)
    state["seen"] = seen[-len(avail_slugs) :]
    state["last_spotlight"] = spotlight.slug
    state["updated"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    _save_state(state_path, state)
    return spotlight


def _window_selection(
    ordered_availability: Sequence[ShowdownAvailability],
    spotlight: ShowdownAvailability,
    window_size: int,
) -> List[ShowdownAvailability]:
    if not ordered_availability:
        return []

    window_size = max(1, window_size)
    if window_size > len(ordered_availability):
        return list(ordered_availability)

    try:
        spotlight_idx = ordered_availability.index(spotlight)
    except ValueError:
        spotlight_idx = 0

    half = window_size // 2
    start = max(0, spotlight_idx - half)
    end = start + window_size
    if end > len(ordered_availability):
        end = len(ordered_availability)
        start = max(0, end - window_size)

    return list(ordered_availability[start:end])


def _build_manifest_entries(
    window_availability: Sequence[ShowdownAvailability],
    spotlight_slug: str,
    label: str,
) -> Dict[str, Dict[str, Any]]:
    collections: Dict[str, Dict[str, Any]] = {}

    for index, availability in enumerate(window_availability):
        is_spotlight = availability.slug == spotlight_slug
        config: Dict[str, Any] = {
            "label": label,
            "letterboxd_list": availability.letterboxd_url,
            "collection_order": "custom",
            "sort_title": (
                f"Showdown {index:02d} "
                f"{availability.available_entries:02d}/{availability.total_entries:02d} "
                f"{availability.title}"
            ),
            "sync_mode": "sync",
            "summary": (
                f"{availability.available_entries}/{availability.total_entries} titles owned "
                f"({availability.match_ratio:.0%})."
            ),
            "visible_library": True,
            "visible_home": is_spotlight,
            "visible_shared": is_spotlight,
        }
        collections[availability.title] = config

    return collections


def _write_manifest(
    output_path: str | Path,
    collections: Dict[str, Dict[str, Any]],
    config_source: str | Path,
    spotlight: ShowdownAvailability,
    window_size: int,
    label: str,
) -> None:
    destination = Path(output_path).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header_lines = [
        "# Managed by lists.showdown_plex",
        f"# Generated on {timestamp}",
        f"# Source config: {Path(config_source).expanduser()}",
        f"# Spotlight: {spotlight.title} ({spotlight.slug})",
        f"# Window size: {len(collections)} (label: {label})",
        "",
    ]

    with destination.open("w", encoding="utf-8") as handle:
        handle.write("\n".join(header_lines))
        yaml.safe_dump(
            {"collections": collections},
            handle,
            sort_keys=False,
            allow_unicode=True,
        )


def main(argv: Optional[Sequence[str]] = None) -> None:  # pragma: no cover - CLI glue
    args = _build_cli_parser().parse_args(argv)

    config_path = Path(args.config).expanduser()
    showdown_json = resolve_path(args.showdown_json, config_path.parent)
    manifest_output = (
        resolve_path(args.manifest_output, config_path.parent)
        if args.manifest_output
        else None
    )
    state_path = (
        resolve_path(args.state_file, config_path.parent) if args.state_file else None
    )

    run_showdown_job(
        letterboxd_config_path=config_path,
        showdown_json_path=showdown_json,
        threshold=args.threshold,
        sort_mode=args.sort,
        manifest_output=manifest_output,
        window=args.window,
        label=args.label,
        state_file=state_path,
        library_override=args.library,
    )


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    main()
