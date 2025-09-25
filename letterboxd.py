import argparse
import datetime
import os
import re
import sys
from pathlib import Path

import requests
import yaml
from bs4 import BeautifulSoup

from lists.dated import generate_dated_collections, get_dated_lists
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


def fetch_user_lists(username, timeout):
    lists = []
    page = 1

    while True:
        url = (
            f"https://letterboxd.com/{username}/lists/page/{page}/"
            if page > 1
            else f"https://letterboxd.com/{username}/lists/"
        )
        print(f"Fetching lists from: {url}")

        try:
            response = requests.get(url, timeout=timeout)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")

            page_lists = []
            for link in soup.find_all("a", href=re.compile(r"^/[^/]+/list/[^/]+/$")):
                list_name = link.text.strip()
                list_url_suffix = link.get("href")
                if not list_name or not list_url_suffix:
                    continue

                parent = link.find_parent()
                tags = (
                    [tag.text for tag in parent.find_all("a", class_="tag")]
                    if parent
                    else []
                )
                page_lists.append((list_name, list_url_suffix, tags))

            if not page_lists:
                break

            lists.extend(page_lists)
            print(f"Found {len(page_lists)} lists on page {page}")
            page += 1

        except requests.exceptions.RequestException as exc:
            print(f"Error fetching lists from {url}: {exc}", file=sys.stderr)
            return None
        except Exception as exc:
            print(f"Unexpected error during list fetching: {exc}", file=sys.stderr)
            return None

    print(f"Total found: {len(lists)} lists across {page - 1} pages.")
    return lists


def update_config_file(config_file_path, all_collections, config_source_label):
    config_file_path = Path(config_file_path).expanduser()
    try:
        with config_file_path.open("r") as file:
            content = file.read()
    except FileNotFoundError:
        print(
            f"Error: Kometa config file not found at {config_file_path}",
            file=sys.stderr,
        )
        return
    except Exception as exc:
        print(
            f"Error reading Kometa config file {config_file_path}: {exc}",
            file=sys.stderr,
        )
        return

    start_marker = "#lbd-lists-begin"
    end_marker = "#lbd-lists-end"
    pattern = re.compile(
        r"^\s*" + re.escape(start_marker) + r".*?^\s*" + re.escape(end_marker),
        re.DOTALL | re.MULTILINE,
    )

    script_name = Path(__file__).name
    config_source_name = Path(config_source_label).name

    new_section_content = f"  {start_marker}\n"
    date_stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        import subprocess

        kometa_hash = (
            subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=os.path.expanduser("/opt/kometa"),
                stderr=subprocess.DEVNULL,
            )
            .decode()
            .strip()
        )
    except Exception:
        kometa_hash = "unknown"

    new_section_content += f"  # Generated by {script_name} on {date_stamp}\n"
    new_section_content += f"  # Kometa git hash: {kometa_hash}\n"
    new_section_content += f"  # This section is managed by {script_name}\n"
    new_section_content += f"  # Configuration loaded from {config_source_name}\n"
    new_section_content += (
        "  # Any manual changes within the markers will be overwritten.\n\n"
    )

    for collection_name, collection_config in all_collections.items():
        new_section_content += f'  "{collection_name}":\n'
        for key, value in collection_config.items():
            if key.startswith("#"):
                new_section_content += f"    {key}: {value}\n"
            elif isinstance(value, list):
                new_section_content += f"    {key}:\n"
                for item in value:
                    new_section_content += f"      - {item}\n"
            elif isinstance(value, bool):
                new_section_content += f"    {key}: {str(value).lower()}\n"
            else:
                new_section_content += f"    {key}: {value}\n"
        new_section_content += "\n"

    new_section_content += f"  {end_marker}"

    if not pattern.search(content):
        error_msg = (
            "Error: Kometa config file "
            f"{config_file_path} does not contain the required markers "
            f"'{start_marker}' and '{end_marker}'."
        )
        print(error_msg, file=sys.stderr)
        return

    updated_content = pattern.sub(new_section_content, content)

    lines = [line.rstrip() for line in updated_content.split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    updated_content = "\n".join(lines) + "\n"

    try:
        with config_file_path.open("w") as file:
            file.write(updated_content)
        print(f"\nKometa config file {config_file_path} has been updated successfully.")
    except Exception as exc:
        print(
            f"Error writing to Kometa config file {config_file_path}: {exc}",
            file=sys.stderr,
        )


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

    update_config_file(kometa_destination, all_collections, config_path)


if __name__ == "__main__":
    main()
