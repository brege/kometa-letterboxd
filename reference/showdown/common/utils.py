"""Miscellaneous helpers shared across list plugins."""

from __future__ import annotations

from pathlib import Path


def resolve_path(value: str | Path, base_dir: Path) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = (base_dir / candidate).resolve()
    return candidate
