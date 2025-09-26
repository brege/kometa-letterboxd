"""Helpers for generating tagged Letterboxd collections."""

from __future__ import annotations

from typing import Mapping

from common.kometa import build_collection_entry
from .lists import to_letterboxd_url


def get_lists_with_tag(all_lists, tag):
    if not tag:
        print("No tagged list tag configured.")
        return []

    tagged_lists = []
    print(f"\nFiltering for lists with tag '{tag}':")
    for title, url_suffix, tags in all_lists:
        if tag in tags:
            print(f"- Found tagged list: {title}")
            tagged_lists.append((title, url_suffix))
    return tagged_lists


def generate_tagged_collections(
    tagged_lists,
    extra: Mapping[str, object] | None = None,
):
    collections = {}

    if not tagged_lists:
        return collections

    print("\nPreparing tagged list collections for config...")

    base_extra = {
        "# visible_library": "true/false # <-- Configure visibility manually",
        "# visible_home": "true/false # <-- Configure visibility manually",
        "# visible_shared": "true/false # <-- Configure visibility manually",
    }
    merged_extra = dict(base_extra)
    if isinstance(extra, Mapping):
        merged_extra.update(extra)

    for title, url_suffix in tagged_lists:
        print(f"- Adding tagged list '{title}' collection placeholder")
        sort_title = f"Z-TAG {title}"

        collections[title] = build_collection_entry(
            to_letterboxd_url(url_suffix),
            sort_title=sort_title,
            extra=merged_extra,
        )

    return collections
