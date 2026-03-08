import argparse
import os
from pathlib import Path

import yaml
from pydantic import ValidationError

from kometa_letterboxd.collectors.featured.showdown import generate_showdown_collections
from kometa_letterboxd.collectors.user.dated import (
    generate_dated_collections,
    get_dated_lists,
)
from kometa_letterboxd.collectors.user.lists import ensure_user_lists
from kometa_letterboxd.collectors.user.tagged import (
    generate_tagged_collections,
    get_lists_with_tag,
)
from kometa_letterboxd.common.config import load_config, resolve_path
from kometa_letterboxd.common.kometa import write_collections_section


def parse_args():
    parser = argparse.ArgumentParser(
        description="generate Kometa collections from Letterboxd lists"
    )
    parser.add_argument("-c", "--config", help="path to configuration file")
    parser.add_argument("-d", "--data", help="path to data directory for caches")
    return parser.parse_args()


def determine_config_path(cli_path):
    candidate = cli_path or os.environ.get("LETTERBOXD_HELPER_CONFIG")
    if candidate:
        return Path(candidate).expanduser()
    raise SystemExit(
        "Error: no configuration path provided. Use --config or set "
        "LETTERBOXD_HELPER_CONFIG."
    )


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


def resolve_required_path(raw_path: str | Path, base_path: Path) -> Path:
    resolved = resolve_path(raw_path, base_path)
    if resolved is None:
        raise ValueError(f"Unable to resolve path {raw_path}")
    return resolved


def main():
    args = parse_args()
    config_path = determine_config_path(args.config)
    try:
        config = load_config(config_path)
    except FileNotFoundError as exc:
        raise SystemExit(
            f"Error: configuration file not found at {exc.filename}"
        ) from exc
    except yaml.YAMLError as exc:
        raise SystemExit(
            f"Error parsing configuration file {config_path}: {exc}"
        ) from exc
    except ValidationError as exc:
        raise SystemExit(str(exc)) from exc

    data_dir = args.data or os.environ.get("LETTERBOXD_HELPER_DATA") or "data"
    lists_cache_path = config.lists_cache or f"{data_dir}/user/dated.json"
    kometa_config_path = resolve_path(config.kometa.config_path, config_path.parent)
    kometa_destination = resolve_required_path(
        config.dated.kometa_destination,
        config_path.parent,
    )

    print("Starting Letterboxd list fetcher...")

    default_destination = ensure_kometa_file(kometa_destination)
    all_user_lists = ensure_user_lists(
        config.username,
        cache_path=lists_cache_path,
        timeout=config.request_timeout,
        refresh=config.refresh_lists,
        days_before=config.dated.days_before,
    )

    all_collections = {}

    dated_lists = get_dated_lists(
        all_user_lists,
        config.dated.letterboxd_prefix,
        config.dated.days_before,
    )
    if dated_lists:
        dated_collections = generate_dated_collections(
            dated_lists,
            config.dated.letterboxd_prefix,
            config.dated.plex_prefix,
            config.dated.days_before,
            entry_extra=config.dated.collection_extra,
            extended_extra=config.dated.extended_extra,
        )
        all_collections.update(dated_collections)

    tagged_lists = get_lists_with_tag(all_user_lists, config.tagged.tag)
    if tagged_lists:
        tagged_collections = generate_tagged_collections(
            tagged_lists,
            extra=config.tagged.extra,
        )
        all_collections.update(tagged_collections)

    showdown_delete: list[str] = []

    showdown_collections, showdown_destination, showdown_retired = (
        generate_showdown_collections(
            all_user_lists,
            config.showdown,
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
            ensured_target = ensure_kometa_file(target_path)
            write_collections_section(
                ensured_target,
                showdown_collections,
                generator=f"{Path(__file__).name} showdown",
                config_source=config_path,
                delete_collections_named=showdown_retired,
            )
            print(f"Showdown collections written to {ensured_target}")
            showdown_delete = []

    delete_collections_named: list[str] = []
    if showdown_delete:
        delete_collections_named.extend(showdown_delete)

    write_collections_section(
        default_destination,
        all_collections,
        generator=Path(__file__).name,
        config_source=config_path,
        delete_collections_named=delete_collections_named or None,
    )
    print(f"\nKometa config file {kometa_destination} has been updated successfully.")


if __name__ == "__main__":
    main()
