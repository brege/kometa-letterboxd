"""Utilities for resolving Plex connectivity using existing Kometa configs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional, Set, TYPE_CHECKING

import yaml

if TYPE_CHECKING:  # pragma: no cover
    from plexapi.server import PlexServer

DEFAULT_PLEX_URL = "http://localhost:32400"
DEFAULT_PLEX_TIMEOUT = 60


@dataclass
class PlexConfig:
    url: str
    token: str
    timeout: int = DEFAULT_PLEX_TIMEOUT
    library: str = "Movies"


def _load_yaml(path: Path) -> Dict[str, object]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Unexpected YAML structure in {path}")
    return data


def load_letterboxd_config(config_path: str | Path) -> Dict[str, object]:
    expanded = Path(config_path).expanduser()
    return _load_yaml(expanded)


def resolve_plex_config(
    kometa_config_path: str | Path,
    library_override: Optional[str] = None,
) -> PlexConfig:
    kometa_path = Path(kometa_config_path).expanduser()
    if not kometa_path.exists():
        raise FileNotFoundError(f"Kometa configuration not found at {kometa_path}")

    kometa_config = _load_yaml(kometa_path)

    if not isinstance(kometa_config, dict):
        raise ValueError(f"Unexpected Kometa configuration structure in {kometa_path}")

    plex_block = kometa_config.get("plex")
    if not isinstance(plex_block, dict):
        raise ValueError(f"Kometa config '{kometa_path}' is missing a 'plex' section")

    token = plex_block.get("token")
    if not token:
        raise ValueError(f"Kometa config '{kometa_path}' is missing 'plex.token'")

    url = str(plex_block.get("url", DEFAULT_PLEX_URL))
    timeout = int(plex_block.get("timeout", DEFAULT_PLEX_TIMEOUT))

    libraries_block = kometa_config.get("libraries")
    library_name: Optional[str] = library_override

    if library_name:
        if isinstance(libraries_block, dict) and library_name not in libraries_block:
            raise ValueError(
                f"Library '{library_name}' not defined in Kometa config '{kometa_path}'"
            )
    else:
        if isinstance(libraries_block, dict):
            if "Movies" in libraries_block:
                library_name = "Movies"
            elif len(libraries_block) == 1:
                library_name = next(iter(libraries_block))
            else:
                raise ValueError(
                    "Multiple libraries configured; specify one via --library"
                    " or supply a library override."
                )
        else:
            raise ValueError(
                f"Kometa config '{kometa_path}' lacks a 'libraries' definition to "
                f"infer a library"
            )

    return PlexConfig(
        url=url,
        token=str(token),
        timeout=timeout,
        library=str(library_name),
    )


def connect_to_plex(
    config: PlexConfig,
) -> "PlexServer":  # pragma: no cover - network I/O
    from plexapi.server import PlexServer

    return PlexServer(config.url, config.token, timeout=config.timeout)


def build_tmdb_library_index(library) -> Set[str]:
    tmdb_ids: Set[str] = set()
    for item in library.all():
        tmdb_id = extract_tmdb_id_from_item(item)
        if tmdb_id:
            tmdb_ids.add(tmdb_id)
    return tmdb_ids


def extract_tmdb_id_from_item(item) -> Optional[str]:  # pragma: no cover - thin wrapper
    if not hasattr(item, "guids"):
        return None
    for guid in getattr(item, "guids", []):
        value = getattr(guid, "id", "")
        if isinstance(value, str) and value.startswith("tmdb://"):
            return value.split("//", 1)[1]
    return None


def count_available_tmdb_ids(
    tmdb_ids: Iterable[str],
    library_index: Set[str],
) -> int:
    return sum(1 for tmdb_id in tmdb_ids if tmdb_id in library_index)
