import argparse
import datetime
import os
import sys
from pathlib import Path

import yaml

from lists.dated import generate_dated_collections, get_dated_lists
from lists.kometa import write_collections_section
from lists.letterboxd_lists import fetch_user_lists
from lists.showdown import generate_showdown_collections
from lists.tagged import generate_tagged_collections, get_lists_with_tag


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


def main():
    args = parse_args()
    config_path = determine_config_path(args.config)
    config = load_config(config_path)

    if not config:
        sys.exit(1)

    username = config.get("username")
    request_timeout = config.get("request_timeout", 30)

    dated_cfg = config.get("dated", {})
    kometa_destination = dated_cfg.get("kometa_destination")
    letterboxd_prefix = dated_cfg.get("letterboxd_prefix", "")
    plex_prefix = dated_cfg.get("plex_prefix", "")
    days_before = dated_cfg.get("days_before", 0)

    tagged_cfg = config.get("tagged", {})
    tag = tagged_cfg.get("tag", "")

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

    all_user_lists = fetch_user_lists(username, request_timeout)

    if all_user_lists is None:
        print(
            "Failed to retrieve user lists. Kometa config file not updated.",
            file=sys.stderr,
        )
        sys.exit(1)

    all_collections = {}

    dated_lists = get_dated_lists(all_user_lists, letterboxd_prefix, days_before)
    if dated_lists:
        dated_collections = generate_dated_collections(
            dated_lists,
            letterboxd_prefix,
            plex_prefix,
            days_before,
        )
        all_collections.update(dated_collections)

    tagged_lists = get_lists_with_tag(all_user_lists, tag)
    if tagged_lists:
        tagged_collections = generate_tagged_collections(tagged_lists)
        all_collections.update(tagged_collections)

    showdown_collections = generate_showdown_collections(all_user_lists, showdown_cfg)
    if showdown_collections:
        all_collections.update(showdown_collections)

    try:
        write_collections_section(
            kometa_destination,
            all_collections,
            generator=Path(__file__).name,
            config_source=config_path,
        )
        print(f"\nKometa config file {kometa_destination} has been updated successfully.")
    except Exception as exc:
        print(
            f"Error updating Kometa config file {kometa_destination}: {exc}",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
