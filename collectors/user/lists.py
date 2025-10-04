"""Utilities for obtaining a user's Letterboxd lists."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, List, Tuple

import requests
from bs4 import BeautifulSoup

from common.cache import load_lists, save_lists

LETTERBOXD_BASE = "https://letterboxd.com"
LIST_HREF_PATTERN = re.compile(r"^/[^/]+/list/[^/]+/$")


def _full_url(path_fragment: str) -> str:
    if path_fragment.startswith("http://") or path_fragment.startswith("https://"):
        return path_fragment
    if not path_fragment.startswith("/"):
        path_fragment = f"/{path_fragment}"
    return f"{LETTERBOXD_BASE}{path_fragment}"


def fetch_user_lists(
    username: str,
    *,
    timeout: int = 30,
    session: requests.Session | None = None,
) -> List[Tuple[str, str, List[str]]]:
    if not username:
        raise ValueError("Username is required to fetch Letterboxd lists")

    owns_session = session is None
    ses = session or requests.Session()

    lists: List[Tuple[str, str, List[str]]] = []
    page = 1

    try:
        while True:
            if page == 1:
                url = f"{LETTERBOXD_BASE}/{username}/lists/"
            else:
                url = f"{LETTERBOXD_BASE}/{username}/lists/page/{page}/"

            response = ses.get(url, timeout=timeout)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")
            page_lists: List[Tuple[str, str, List[str]]] = []

            for link in soup.find_all("a", href=LIST_HREF_PATTERN):
                title = (link.text or "").strip()
                href = link.get("href") or ""
                if not title or not href:
                    continue

                parent = link.find_parent()
                tags: Iterable[str]
                if parent:
                    tags = [tag.text for tag in parent.find_all("a", class_="tag")]
                else:
                    tags = []

                page_lists.append((title, href, list(tags)))

            if not page_lists:
                break

            lists.extend(page_lists)
            page += 1
    finally:
        if owns_session:
            ses.close()

    return lists


def ensure_user_lists(
    username: str,
    *,
    cache_path: str | Path | None = None,
    timeout: int = 30,
    refresh: bool = False,
) -> List[Tuple[str, str, List[str]]]:
    path = Path(cache_path).expanduser() if cache_path else None

    if path and not refresh:
        cached = load_lists(path)
        if cached:
            return [
                (
                    str(item.get("title")),
                    str(item.get("url_suffix")),
                    list(item.get("tags", [])),
                )
                for item in cached
            ]

    lists = fetch_user_lists(username, timeout=timeout)

    if path:
        serializable = [
            {
                "title": title,
                "url_suffix": url_suffix,
                "tags": list(tags),
            }
            for title, url_suffix, tags in lists
        ]
        save_lists(path, serializable)

    return lists


def to_letterboxd_url(url_suffix: str) -> str:
    return _full_url(url_suffix)
