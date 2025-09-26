"""Command-line interface for Letterboxd tools."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Sequence

from .config import load_config
from .dated import generate_dated_collections, get_dated_lists
from .kometa import write_collections_section
from .letterboxd_lists import fetch_user_lists
from .showdown_plex import run_showdown_from_config
from .tagged import generate_tagged_collections, get_lists_with_tag
from .utils import resolve_path


def merge_overrides(
    target: dict[str, Any], overrides: dict[str, Any]
) -> dict[str, Any]:
    updated = dict(target)
    for key, value in overrides.items():
        if value is not None:
            updated[key] = value
    return updated


def run_dated(
    global_config: dict[str, Any],
    dated_config: dict[str, Any],
    tagged_config: dict[str, Any],
    config_path: Path,
) -> None:
    username = global_config.get("username")
    if not username:
        raise ValueError("Config missing 'username'")

    timeout = int(global_config.get("request_timeout", 30))
    all_lists = fetch_user_lists(username, timeout)
    if all_lists is None:
        raise RuntimeError("Failed to fetch Letterboxd lists")

    letterboxd_prefix = dated_config.get("letterboxd_prefix", "")
    plex_prefix = dated_config.get("plex_prefix", "")
    days_before = int(dated_config.get("days_before", 0))

    destination_raw = dated_config.get("kometa_destination") or dated_config.get(
        "output"
    )
    if not destination_raw:
        raise ValueError("'dated.kometa_destination' must be configured")

    output_path = resolve_path(destination_raw, config_path.parent)

    dated_lists = get_dated_lists(all_lists, letterboxd_prefix, days_before)
    collections = generate_dated_collections(
        dated_lists,
        letterboxd_prefix,
        plex_prefix,
        days_before,
    )

    tag_value = tagged_config.get("tag")
    if tag_value:
        tagged_lists = get_lists_with_tag(all_lists, tag_value)
        tagged_collections = generate_tagged_collections(tagged_lists)
        collections.update(tagged_collections)

    write_collections_section(
        output_path,
        collections,
        generator="lists.cli dated",
        config_source=config_path,
    )
    print(f"Updated {output_path}")


def run_tagged(
    global_config: dict[str, Any],
    tagged_config: dict[str, Any],
    config_path: Path,
) -> None:
    username = global_config.get("username")
    if not username:
        raise ValueError("Config missing 'username'")

    tag_value = tagged_config.get("tag")
    if not tag_value:
        raise ValueError("'tagged.tag' must be configured")

    timeout = int(global_config.get("request_timeout", 30))
    all_lists = fetch_user_lists(username, timeout)
    if all_lists is None:
        raise RuntimeError("Failed to fetch Letterboxd lists")

    tagged_lists = get_lists_with_tag(all_lists, tag_value)
    if not tagged_lists:
        print(f"No lists tagged '{tag_value}' found.")
        return

    collections = generate_tagged_collections(tagged_lists)

    print(f"Tagged collections for '{tag_value}':")
    for name, info in collections.items():
        url = info.get("letterboxd_list")
        print(f"- {name}: {url}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Letterboxd list tooling")
    parser.add_argument(
        "--config",
        default="config.yml",
        help="Path to the Letterboxd tooling config",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    dated = subparsers.add_parser(
        "dated", help="Generate Kometa film-club collections from dated lists"
    )
    dated.add_argument("--output", help="Override Kometa destination path")
    dated.add_argument("--letterboxd-prefix", help="Override Letterboxd prefix")
    dated.add_argument("--plex-prefix", help="Override Plex collection prefix")
    dated.add_argument(
        "--days-before", type=int, help="Offset current month calculation"
    )

    tagged = subparsers.add_parser(
        "tagged", help="Show Letterboxd lists tagged for Kometa inclusion"
    )
    tagged.add_argument("--tag", help="Override tag filter")

    showdown = subparsers.add_parser(
        "showdown", help="Generate Plex spotlight manifest from showdown datasets"
    )
    showdown.add_argument("--showdown-json", help="Override showdown dataset path")
    showdown.add_argument("--threshold", type=int, help="Minimum owned titles")
    showdown.add_argument("--sort", help="Sort mode for availability table")
    showdown.add_argument("--manifest-output", help="Override manifest output path")
    showdown.add_argument("--window", type=int, help="Spotlight window size")
    showdown.add_argument("--label", help="Manifest label for Kometa collections")
    showdown.add_argument("--library", help="Override Kometa library name")
    showdown.add_argument("--state-file", help="Path to spotlight state file")

    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    config_path = Path(args.config).expanduser()
    config = load_config(config_path)

    if args.command == "dated":
        dated_config = config.get("dated", {}) if isinstance(config, dict) else {}
        tagged_config = config.get("tagged", {}) if isinstance(config, dict) else {}
        overrides = {
            "kometa_destination": args.output,
            "letterboxd_prefix": args.letterboxd_prefix,
            "plex_prefix": args.plex_prefix,
            "days_before": args.days_before,
        }
        merged = merge_overrides(
            dated_config if isinstance(dated_config, dict) else {}, overrides
        )
        run_dated(
            config,
            merged,
            tagged_config if isinstance(tagged_config, dict) else {},
            config_path,
        )
        return

    if args.command == "tagged":
        tagged_config = config.get("tagged", {}) if isinstance(config, dict) else {}
        overrides = {"tag": args.tag}
        merged = merge_overrides(
            tagged_config if isinstance(tagged_config, dict) else {}, overrides
        )
        run_tagged(config, merged, config_path)
        return

    if args.command == "showdown":
        showdown_config = config.get("showdown", {}) if isinstance(config, dict) else {}
        overrides = {
            "showdown_json": args.showdown_json,
            "threshold": args.threshold,
            "sort": args.sort,
            "manifest_output": args.manifest_output,
            "window": args.window,
            "label": args.label,
            "library": args.library,
            "state_file": args.state_file,
        }
        merged = merge_overrides(
            showdown_config if isinstance(showdown_config, dict) else {}, overrides
        )
        run_showdown_from_config(merged, letterboxd_config_path=config_path)
        return

    raise ValueError(f"Unhandled command: {args.command}")


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    main()
