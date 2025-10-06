import argparse
import os
import sys
from pathlib import Path

import yaml

from collectors.featured.showdown import generate_showdown_collections
from collectors.user.dated import generate_dated_collections, get_dated_lists
from collectors.user.lists import ensure_user_lists
from collectors.user.tagged import generate_tagged_collections, get_lists_with_tag
from common.kometa import write_collections_section


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate Kometa collections from Letterboxd lists"
    )
    parser.add_argument(
        "--config",
        help=(
            "Path to the Letterboxd helper configuration file. "
            "If omitted, the script falls back to the"
            " $LETTERBOXD_HELPER_CONFIG environment variable."
        ),
    )
    return parser.parse_args()


def determine_config_path(cli_path):
    if cli_path:
        candidate = Path(cli_path).expanduser()
        if candidate.exists():
            return candidate
        print(f"Error: configuration file not found at {candidate}", file=sys.stderr)
        sys.exit(1)

    env_value = os.environ.get("LETTERBOXD_HELPER_CONFIG")
    if env_value:
        candidate = Path(env_value).expanduser()
        if candidate.exists():
            return candidate
        print(f"Error: configuration file not found at {candidate}", file=sys.stderr)
        sys.exit(1)

    msg = (
        "Error: no configuration path provided. Use --config or set "
        "LETTERBOXD_HELPER_CONFIG."
    )
    print(msg, file=sys.stderr)
    sys.exit(1)


def load_config(config_path):
    try:
        with config_path.open("r") as file:
            config = yaml.safe_load(file)
        print(f"Configuration loaded from {config_path}")
        return config
    except FileNotFoundError:
        print(f"Error: configuration file not found at {config_path}", file=sys.stderr)
        return None
    except yaml.YAMLError as exc:
        print(f"Error parsing configuration file {config_path}: {exc}", file=sys.stderr)
        return None
    except Exception as exc:
        print(f"Unexpected error loading config {config_path}: {exc}", file=sys.stderr)
        return None


def ensure_kometa_file(path: Path) -> Path:
    expanded = Path(path).expanduser()
    if expanded.exists():
        return expanded

    expanded.parent.mkdir(parents=True, exist_ok=True)
    with expanded.open("w", encoding="utf-8") as handle:
        handle.write("# Initialized by kometa-letterboxd\n\n")
        yaml.safe_dump(
            {"collections": {}},
            handle,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
            width=120,
            indent=2,
        )
    return expanded


def main():
    args = parse_args()
    config_path = determine_config_path(args.config)
    config = load_config(config_path)

    if not config:
        sys.exit(1)

    username = config.get("username")
    request_timeout = config.get("request_timeout", 30)
    lists_cache_path = config.get("lists_cache", "data/user/dated.json")
    refresh_lists = bool(config.get("refresh_lists", False))

    kometa_cfg = config.get("kometa", {})
    kometa_config_path: Path | None = None
    if isinstance(kometa_cfg, dict):
        raw_kometa_path = kometa_cfg.get("config_path")
        if raw_kometa_path:
            expanded = Path(str(raw_kometa_path)).expanduser()
            if not expanded.is_absolute():
                expanded = (config_path.parent / expanded).resolve()
            kometa_config_path = expanded

    dated_cfg = config.get("dated", {})
    kometa_destination = dated_cfg.get("kometa_destination") or dated_cfg.get(
        "kometa_target"
    )
    letterboxd_prefix = dated_cfg.get("letterboxd_prefix", "")
    plex_prefix = dated_cfg.get("plex_prefix", "")
    days_before = dated_cfg.get("days_before", 0)
    raw_collection_extra = dated_cfg.get("collection_extra")
    entry_extra = raw_collection_extra if isinstance(raw_collection_extra, dict) else {}
    raw_extended_extra = dated_cfg.get("extended_extra")
    extended_extra = raw_extended_extra if isinstance(raw_extended_extra, dict) else {}

    tagged_cfg = config.get("tagged", {})
    tag = tagged_cfg.get("tag", "")
    raw_tagged_extra = tagged_cfg.get("extra")
    tagged_extra = raw_tagged_extra if isinstance(raw_tagged_extra, dict) else {}

    showdown_cfg = config.get("showdown", {})

    if not username:
        print("Error: Letterboxd username not specified in config.", file=sys.stderr)
        sys.exit(1)

    if not kometa_destination:
        print(
            "Error: 'dated.kometa_destination' not specified in config.",
            file=sys.stderr,
        )
        sys.exit(1)

    print("Starting Letterboxd list fetcher...")

    default_destination = ensure_kometa_file(kometa_destination)

    try:
        all_user_lists = ensure_user_lists(
            username,
            cache_path=lists_cache_path,
            timeout=request_timeout,
            refresh=refresh_lists,
        )
    except Exception as exc:
        print(
            "Failed to retrieve user lists. Kometa config file not updated.",
            file=sys.stderr,
        )
        print(f"Details: {exc}", file=sys.stderr)
        sys.exit(1)

    all_collections = {}

    dated_lists = get_dated_lists(all_user_lists, letterboxd_prefix, days_before)
    if dated_lists:
        dated_collections = generate_dated_collections(
            dated_lists,
            letterboxd_prefix,
            plex_prefix,
            days_before,
            entry_extra=entry_extra,
            extended_extra=extended_extra,
        )
        all_collections.update(dated_collections)

    tagged_lists = get_lists_with_tag(all_user_lists, tag)
    if tagged_lists:
        tagged_collections = generate_tagged_collections(
            tagged_lists,
            extra=tagged_extra,
        )
        all_collections.update(tagged_collections)

    showdown_delete: list[str] = []

    showdown_collections, showdown_destination, showdown_retired = (
        generate_showdown_collections(
            all_user_lists,
            showdown_cfg,
            base_path=config_path.parent,
            kometa_config_path=kometa_config_path,
            config_source=config_path,
        )
    )
    showdown_retired = list(dict.fromkeys(showdown_retired))
    if showdown_collections:
        target_path = showdown_destination or default_destination
        if target_path == default_destination:
            all_collections.update(showdown_collections)
            showdown_delete = showdown_retired
        else:
            try:
                ensured_target = ensure_kometa_file(target_path)
                write_collections_section(
                    ensured_target,
                    showdown_collections,
                    generator=f"{Path(__file__).name} showdown",
                    config_source=config_path,
                    delete_collections_named=showdown_retired,
                )
                print(f"Showdown collections written to {ensured_target}")
            except Exception as exc:
                print(
                    f"Error updating showdown Kometa file {target_path}: {exc}",
                    file=sys.stderr,
                )
            showdown_delete = []

    delete_collections_named: list[str] = []
    if showdown_delete:
        delete_collections_named.extend(showdown_delete)

    try:
        write_collections_section(
            default_destination,
            all_collections,
            generator=Path(__file__).name,
            config_source=config_path,
            delete_collections_named=delete_collections_named or None,
        )
        print(
            f"\nKometa config file {kometa_destination} has been updated successfully."
        )
    except Exception as exc:
        print(
            f"Error updating Kometa config file {kometa_destination}: {exc}",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
