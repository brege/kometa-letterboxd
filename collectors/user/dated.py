"""Helpers for generating dated Letterboxd collections."""

from __future__ import annotations

import datetime
from typing import Mapping

from common.kometa import build_collection_entry
from .lists import to_letterboxd_url


def parse_dated_list_title(title, prefix):
    if prefix and title.startswith(prefix):
        try:
            date_part_str = title[len(prefix) :].strip()
            return datetime.datetime.strptime(date_part_str, "%B, %Y").date()
        except (ValueError, IndexError):
            return None
    return None


def get_dated_lists(all_lists, prefix, days_before=0):
    if not prefix:
        print("No dated list prefix configured.")
        return []

    dated_lists_with_date = []
    current_date = datetime.date.today()

    offset_date = current_date + datetime.timedelta(days=days_before)
    effective_current_month_start = offset_date.replace(day=1)

    print(f"\nFiltering for dated lists with prefix '{prefix}':")
    if days_before > 0:
        print(
            "Using"
            f" {days_before} days offset - treating"
            f" {effective_current_month_start.strftime('%B %Y')} as current month"
        )

    for title, url_suffix, _ in all_lists:
        parsed_date = parse_dated_list_title(title, prefix)
        if parsed_date is not None:
            if parsed_date <= current_date.replace(day=1):
                print(f"- Found dated list (past/current): {title}")
            else:
                print(f"- Found dated list (future): {title}")
            dated_lists_with_date.append((parsed_date, title, url_suffix))

    dated_lists_with_date.sort()
    sorted_dated_lists = [
        (title, url_suffix) for _, title, url_suffix in dated_lists_with_date
    ]
    return sorted_dated_lists


def generate_dated_collections(
    dated_lists,
    dated_list_prefix,
    new_collection_prefix,
    days_before=0,
    *,
    entry_extra: Mapping[str, object] | None = None,
    extended_extra: Mapping[str, object] | None = None,
):
    collections = {}
    current_date = datetime.date.today()
    offset_date = current_date + datetime.timedelta(days=days_before)
    effective_current_month_start = offset_date.replace(day=1)

    print("\nPreparing dated list collections for config...")

    for title, url_suffix in dated_lists:
        parsed_date = parse_dated_list_title(title, dated_list_prefix)

        if parsed_date is not None:
            month_year_str = parsed_date.strftime("%B, %Y")
            collection_title = (
                f"{new_collection_prefix} - {month_year_str}"
                if new_collection_prefix
                else f"{dated_list_prefix.strip()} - {month_year_str}"
            )
            if not new_collection_prefix and not dated_list_prefix:
                collection_title = title

            sort_title = parsed_date.strftime("%Y-%m") + f" {collection_title}"

            is_current = parsed_date == effective_current_month_start
            if is_current:
                print(
                    f"- Setting '{collection_title}' to visible "
                    "(effective current month)"
                )
            else:
                print(f"- Setting '{collection_title}' to hidden")

            extra_payload = (
                dict(entry_extra) if isinstance(entry_extra, Mapping) else {}
            )

            collections[collection_title] = build_collection_entry(
                to_letterboxd_url(url_suffix),
                sort_title=sort_title,
                visible_library=is_current,
                visible_home=is_current,
                visible_shared=is_current,
                extra=extra_payload,
            )

    all_months_title = (
        f"{new_collection_prefix} Extended Edition"
        if new_collection_prefix
        else f"{dated_list_prefix.strip()} All Months"
    )
    if not new_collection_prefix and not dated_list_prefix:
        all_months_title = "All Dated Lists"

    extended_sort_title = f"{current_date.year}-99 {all_months_title}"

    extended_payload = (
        dict(extended_extra) if isinstance(extended_extra, Mapping) else {}
    )

    print(f"\nPreparing '{all_months_title}' collection for config...")
    collections[all_months_title] = build_collection_entry(
        [to_letterboxd_url(url_suffix) for _, url_suffix in dated_lists],
        sort_title=extended_sort_title,
        collection_order="release.desc",
        visible_library=True,
        visible_home=False,
        visible_shared=False,
        extra=extended_payload,
    )

    return collections
