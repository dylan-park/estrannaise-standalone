"""Config management — JSON file replaces HA config entries."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class AppConfig(BaseModel):
    units: str = "pg/mL"
    regimens: list[dict[str, Any]] = Field(default_factory=list)


def load_config(path: Path) -> AppConfig:
    if path.exists():
        try:
            data = json.loads(path.read_text())
            return AppConfig(**data)
        except Exception:
            pass
    return AppConfig()


def save_config(path: Path, config: AppConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config.model_dump(), indent=2))
