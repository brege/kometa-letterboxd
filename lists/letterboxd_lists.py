"""Network helpers for retrieving Letterboxd list metadata."""

from __future__ import annotations

import re
from typing import List, Optional, Sequence, Tuple

import requests
from bs4 import BeautifulSoup


def fetch_user_lists(username: str, timeout: int) -> Optional[List[Tuple[str, str, Sequence[str]]]]:
    lists: List[Tuple[str, str, Sequence[str]]] = []
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
        except requests.RequestException as exc:
            print(f"Error fetching lists from {url}: {exc}")
            return None

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

    print(f"Total found: {len(lists)} lists across {page - 1} pages.")
    return lists
