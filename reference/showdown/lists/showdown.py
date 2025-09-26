"""Probe helpers for Letterboxd Showdown integrations.

This module is intentionally written in two layers:
- Probe utilities that fetch Showdown metadata and the corresponding
  "Most mentioned" crew lists to produce a raw dataset we can inspect.
- A placeholder collection generator that will later consume the dataset.

Keeping the probe code alongside the stubbed collection generator lets us
iterate quickly without touching the rest of the Letterboxd tooling until the
rules for Showdown collections are finalized.
"""

from __future__ import annotations

import argparse
import json
import re
import textwrap
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from requests import Session

from .showdown_store import load_cache, save_cache

BASE_URL = "https://letterboxd.com"
SHOWDOWN_ROOT = f"{BASE_URL}/showdown/"
CREW_LIST_TEMPLATE = f"{BASE_URL}/crew/list/showdown-{{slug}}/"
DEFAULT_TIMEOUT = 15
DEFAULT_HEADERS = {
    "User-Agent": "kometa-letterboxd-showdown-probe/0.1 (+https://letterboxd.com/)"
}
_YEAR_PATTERN = re.compile(r"\((\d{4})\)$")
CACHE_PATH = Path("data/showdown_cache.json")


@dataclass
class ShowdownSummary:
    """Metadata for a single Showdown pulled from the index page."""

    slug: str
    title: str
    logline: Optional[str]
    status: Optional[str]
    showdown_url: str
    crew_list_url: str

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "ShowdownSummary":
        return cls(
            slug=str(data.get("slug", "")),
            title=str(data.get("title", "")),
            logline=data.get("logline"),
            status=data.get("status"),
            showdown_url=str(data.get("showdown_url", "")),
            crew_list_url=str(data.get("crew_list_url", "")),
        )


@dataclass
class ShowdownEntry:
    """Represents a single ranked film inside a Showdown crew list."""

    rank: int
    film_name: str
    film_slug: str
    film_year: Optional[int]
    film_url: str
    details_endpoint: Optional[str] = None
    tmdb_id: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "ShowdownEntry":
        rank_value = data.get("rank", 0)
        try:
            rank_int = int(rank_value)
        except (TypeError, ValueError):
            rank_int = 0
        return cls(
            rank=rank_int,
            film_name=str(data.get("film_name", "")),
            film_slug=str(data.get("film_slug", "")),
            film_year=data.get("film_year"),
            film_url=str(data.get("film_url", "")),
            details_endpoint=data.get("details_endpoint"),
            tmdb_id=data.get("tmdb_id"),
        )


@dataclass
class ShowdownDataset:
    """Full data for a Showdown, including parsed crew list entries."""

    summary: ShowdownSummary
    published_at: Optional[str]
    entries: List[ShowdownEntry] = field(default_factory=list)

    @property
    def entry_count(self) -> int:
        return len(self.entries)

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "ShowdownDataset":
        summary_dict = data.get("summary", {}) if isinstance(data, dict) else {}
        summary = ShowdownSummary.from_dict(summary_dict)
        entries_data = data.get("entries", []) if isinstance(data, dict) else []
        entries = []
        for entry_data in entries_data:
            try:
                entries.append(ShowdownEntry.from_dict(entry_data))
            except Exception:
                continue
        return cls(
            summary=summary,
            published_at=data.get("published_at") if isinstance(data, dict) else None,
            entries=entries,
        )


def _ensure_session(session: Optional[Session]) -> Session:
    if session is not None:
        return session
    ses = requests.Session()
    ses.headers.update(DEFAULT_HEADERS)
    return ses


def fetch_html(
    url: str, session: Optional[Session] = None, timeout: int = DEFAULT_TIMEOUT
) -> str:
    ses = _ensure_session(session)
    response = ses.get(url, timeout=timeout)
    response.raise_for_status()
    return response.text


def parse_showdown_index(html: str) -> List[ShowdownSummary]:
    soup = BeautifulSoup(html, "html.parser")
    summaries: List[ShowdownSummary] = []
    seen_slugs = set()

    for anchor in soup.select("section.content-teaser a.image"):
        href = anchor.get("href")
        if not href or not href.startswith("/showdown/"):
            continue
        slug = href.strip("/").split("/")[-1]
        if not slug or slug in seen_slugs:
            continue

        section = anchor.find_parent("section", class_="content-teaser")
        if not section:
            continue

        title_tag = section.select_one("h3 a")
        logline_tag = section.select_one("h4")
        status_tag = section.select_one("span.badge")

        title_text = (
            title_tag.get_text(strip=True)
            if title_tag
            else slug.replace("-", " ").title()
        )
        logline_text = logline_tag.get_text(strip=True) if logline_tag else None
        status_text = status_tag.get_text(strip=True) if status_tag else None

        summaries.append(
            ShowdownSummary(
                slug=slug,
                title=title_text,
                logline=logline_text,
                status=status_text,
                showdown_url=urljoin(BASE_URL, href),
                crew_list_url=CREW_LIST_TEMPLATE.format(slug=slug),
            )
        )
        seen_slugs.add(slug)

    return summaries


def _extract_year_from_name(name: str) -> Optional[int]:
    match = _YEAR_PATTERN.search(name or "")
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            return None
    return None


def parse_showdown_crew_list(html: str) -> tuple[Optional[str], List[ShowdownEntry]]:
    soup = BeautifulSoup(html, "html.parser")

    published_at = None
    published_time = soup.select_one("p.list-date time")
    if published_time and published_time.has_attr("datetime"):
        published_at = published_time["datetime"].strip() or None

    entries: List[ShowdownEntry] = []
    for li in soup.select("li.posteritem"):
        poster = li.select_one("div.react-component")
        if not poster:
            continue

        name = poster.get("data-item-name", "").strip()
        slug = poster.get("data-item-slug", "").strip()
        film_link = poster.get("data-item-link", "").strip()
        year = _extract_year_from_name(name)
        details_endpoint = poster.get("data-details-endpoint")

        rank_text = None
        rank_tag = li.select_one("p.list-number")
        if rank_tag:
            rank_text = rank_tag.get_text(strip=True)
        try:
            rank = int(rank_text) if rank_text else len(entries) + 1
        except ValueError:
            rank = len(entries) + 1

        entries.append(
            ShowdownEntry(
                rank=rank,
                film_name=name or slug,
                film_slug=slug or film_link.strip("/").split("/")[-1],
                film_year=year,
                film_url=urljoin(BASE_URL, film_link) if film_link else "",
                details_endpoint=details_endpoint,
            )
        )

    return published_at, entries


def _extract_tmdb_id_from_film_page(html: str) -> Optional[str]:
    soup = BeautifulSoup(html, "html.parser")
    body_tag = soup.find("body")
    if body_tag and body_tag.has_attr("data-tmdb-id"):
        tmdb_id = body_tag["data-tmdb-id"].strip()
        return tmdb_id or None
    return None


def _populate_tmdb_ids(
    dataset: Sequence[ShowdownDataset],
    *,
    session: Session,
    timeout: int,
    progress: Optional[Callable[[str], None]] = None,
) -> None:
    film_url_to_entries: Dict[str, List[ShowdownEntry]] = {}

    for item in dataset:
        for entry in item.entries:
            if entry.tmdb_id:
                continue
            film_url = entry.film_url or urljoin(BASE_URL, f"/film/{entry.film_slug}/")
            if not film_url:
                continue
            film_url_to_entries.setdefault(film_url, []).append(entry)

    for film_url, entries in film_url_to_entries.items():
        try:
            film_html = fetch_html(film_url, session=session, timeout=timeout)
            tmdb_id = _extract_tmdb_id_from_film_page(film_html)
        except requests.RequestException as exc:
            if progress:
                progress(f"  ! Error fetching film metadata from {film_url}: {exc}")
            tmdb_id = None

        for entry in entries:
            entry.tmdb_id = tmdb_id


def collect_showdown_dataset(
    *,
    limit: Optional[int] = None,
    timeout: int = DEFAULT_TIMEOUT,
    session: Optional[Session] = None,
    on_dataset: Optional[Callable[[ShowdownDataset], None]] = None,
    use_cache: bool = True,
    cache_path: Path = CACHE_PATH,
    force_refresh: bool = False,
    progress: Optional[Callable[[str], None]] = print,
) -> List[ShowdownDataset]:
    """Fetch Showdown metadata plus crew lists for probing.

    Parameters
    ----------
    limit: Optional[int]
        Fetch at most this many Showdown entries (useful during development).
    timeout: int
        Requests timeout in seconds for each HTTP call.
    session: Optional[requests.Session]
        Optional session to reuse connections and override auth headers.
    on_dataset: Optional[Callable[[ShowdownDataset], None]]
        Callback invoked for each dataset as soon as it is available.
    use_cache: bool
        When true, re-use previously fetched datasets stored on disk.
    cache_path: Path
        Location of the showdown dataset cache.
    force_refresh: bool
        Ignore the cache and fetch everything anew.
    """

    ses = _ensure_session(session)

    def emit(message: str) -> None:
        if progress:
            progress(message)

    existing_cache: Dict[str, Dict[str, object]] = {}
    if use_cache and not force_refresh:
        existing_cache = load_cache(cache_path)
        if existing_cache:
            emit(
                f"Loaded {len(existing_cache)} showdown datasets from cache at {cache_path}"
            )

    updated_cache: Dict[str, Dict[str, object]] = {}
    datasets: List[ShowdownDataset] = []

    emit(f"Fetching Showdown index: {SHOWDOWN_ROOT}")
    index_html = fetch_html(SHOWDOWN_ROOT, session=ses, timeout=timeout)
    summaries = parse_showdown_index(index_html)

    if limit is not None:
        summaries = summaries[:limit]

    total = len(summaries)

    for position, summary in enumerate(summaries, start=1):
        status = (summary.status or "").strip().lower()
        if status == "in progress":
            emit(
                f"[{position}/{total}] Skipping '{summary.title}' ({summary.slug})"
                " because it is marked in progress"
            )
            dataset = ShowdownDataset(summary=summary, published_at=None, entries=[])
            datasets.append(dataset)
            if on_dataset:
                on_dataset(dataset)
            continue

        cached_dataset: Optional[ShowdownDataset] = None
        if use_cache and not force_refresh and summary.slug in existing_cache:
            cached_dataset = ShowdownDataset.from_dict(existing_cache[summary.slug])
            if cached_dataset.entry_count == 0:
                cached_dataset = None
            elif any(entry.tmdb_id in (None, "") for entry in cached_dataset.entries):
                _populate_tmdb_ids(
                    [cached_dataset], session=ses, timeout=timeout, progress=progress
                )
            if cached_dataset:
                emit(f"[{position}/{total}] Using cached showdown '{summary.title}'")

        if cached_dataset:
            dataset = cached_dataset
        else:
            emit(f"[{position}/{total}] Fetching '{summary.title}' ({summary.slug})")
            try:
                crew_html = fetch_html(
                    summary.crew_list_url, session=ses, timeout=timeout
                )
            except requests.RequestException as exc:
                emit(f"  ! Error fetching crew list for '{summary.title}': {exc}")
                dataset = ShowdownDataset(
                    summary=summary, published_at=None, entries=[]
                )
            else:
                published_at, entries = parse_showdown_crew_list(crew_html)
                if not entries:
                    emit(
                        "  ! No entries parsed; check page structure or auth requirements"
                    )
                dataset = ShowdownDataset(
                    summary=summary,
                    published_at=published_at,
                    entries=entries,
                )
                _populate_tmdb_ids(
                    [dataset], session=ses, timeout=timeout, progress=progress
                )
                emit(
                    f"[{position}/{total}] Collected {dataset.entry_count} entries for"
                    f" '{summary.title}'"
                )

        if dataset.entry_count:
            updated_cache[summary.slug] = asdict(dataset)

        datasets.append(dataset)
        if on_dataset:
            on_dataset(dataset)

    if use_cache:
        for slug, cached_value in existing_cache.items():
            updated_cache.setdefault(slug, cached_value)
        save_cache(updated_cache, cache_path)
        emit(f"Cached {len(updated_cache)} showdown datasets → {cache_path}")

    return datasets


def _summarize_dataset(dataset: Sequence[ShowdownDataset]) -> None:
    print("")
    print(f"Showdown probe collected {len(dataset)} lists")
    entry_counts = [item.entry_count for item in dataset if item.entries]
    if entry_counts:
        print(
            "Entry counts"
            f" — min: {min(entry_counts)} | max: {max(entry_counts)} | average: "
            f"{sum(entry_counts) / len(entry_counts):.1f}"
        )
    in_progress = sum(1 for item in dataset if item.summary.status)
    if in_progress:
        print(f"Showdowns marked in progress: {in_progress}")

    for item in dataset:
        status = item.summary.status or "completed"
        print(f"- {item.summary.title} [{status}] → {item.entry_count} entries")
        if item.entries:
            for entry in item.entries:
                display_name = entry.film_name
                if entry.film_year and f"({entry.film_year})" not in display_name:
                    display_name = f"{display_name} ({entry.film_year})"
                print(f"    {entry.rank:>2}. {display_name}")


def probe_showdowns(
    *,
    limit: Optional[int] = None,
    timeout: int = DEFAULT_TIMEOUT,
    as_json: bool = False,
    force_refresh: bool = False,
    output_path: Optional[str] = None,
) -> None:
    if as_json:
        if output_path:
            destination = Path(output_path).expanduser()
            destination.parent.mkdir(parents=True, exist_ok=True)

            first_entry = True

            with destination.open("w", encoding="utf-8") as handle:

                def stream_dataset(dataset: ShowdownDataset) -> None:
                    nonlocal first_entry
                    nonlocal handle
                    payload = json.dumps(asdict(dataset), indent=2)
                    indented_payload = textwrap.indent(payload, "  ")

                    if first_entry:
                        handle.write("[\n")
                        first_entry = False
                    else:
                        handle.write(",\n")

                    handle.write(indented_payload)
                    handle.flush()
                    print(f"Captured showdown: {dataset.summary.title}")

                collect_showdown_dataset(
                    limit=limit,
                    timeout=timeout,
                    on_dataset=stream_dataset,
                    force_refresh=force_refresh,
                )

                if first_entry:
                    handle.write("[]\n")
                else:
                    handle.write("\n]\n")
        else:

            def stream_dataset(dataset: ShowdownDataset) -> None:
                print(json.dumps(asdict(dataset), indent=2))

            collect_showdown_dataset(
                limit=limit,
                timeout=timeout,
                on_dataset=stream_dataset,
                force_refresh=force_refresh,
                progress=None,
            )
    else:
        datasets = collect_showdown_dataset(
            limit=limit,
            timeout=timeout,
            force_refresh=force_refresh,
            progress=print,
        )
        _summarize_dataset(datasets)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Probe Letterboxd Showdown datasets")
    parser.add_argument(
        "--limit",
        type=int,
        help="Fetch only the first N Showdowns from the index (useful for quick tests)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help="HTTP timeout (seconds) for each request",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the collected dataset as JSON",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Ignore the cached showdown dataset and fetch everything again",
    )
    parser.add_argument(
        "--output",
        help=(
            "File path to write JSON output. Useful with --json to capture data while"
            " still displaying progress in the console."
        ),
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    probe_showdowns(
        limit=args.limit,
        timeout=args.timeout,
        as_json=args.json,
        force_refresh=args.refresh,
        output_path=args.output,
    )


def generate_showdown_collections(all_lists, showdown_config):
    """Build collections from showdown settings.

    The implementation remains a stub until we define Showdown rules.
    """

    _ = (all_lists, showdown_config)  # quiet linters about unused params
    return {}


if __name__ == "__main__":
    main()
