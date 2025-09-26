"""Lightweight helpers for reading Letterboxd tool configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Dict

import yaml


def load_config(path: str | Path) -> Dict[str, object]:
    config_path = Path(path).expanduser()
    with config_path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping at {config_path}")
    return data
