"""Configuration-driven versioned prompt catalog."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULT_PROMPT_DIR = Path("configs/prompts")


class PromptCatalogError(RuntimeError):
    """Prompt configuration could not be loaded or validated."""


def prompt_dir() -> Path:
    return Path(os.environ.get("PROMPT_CONFIG_DIR", str(DEFAULT_PROMPT_DIR)))


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PromptCatalogError(f"prompt config not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise PromptCatalogError(f"invalid prompt JSON: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise PromptCatalogError(f"prompt config must be a JSON object: {path}")
    return data


def active_prompt_ids() -> dict[str, str]:
    data = _read_json(prompt_dir() / "active.json")
    result: dict[str, str] = {}
    for stage, prompt_id in data.items():
        if not isinstance(stage, str) or not isinstance(prompt_id, str) or not prompt_id:
            raise PromptCatalogError("active.json must map stage names to non-empty prompt IDs")
        result[stage] = prompt_id
    return result


def load_prompt(stage: str, *, prompt_id: str | None = None) -> dict[str, Any]:
    active = active_prompt_ids()
    selected = prompt_id or active.get(stage)
    if not selected:
        raise PromptCatalogError(f"no active prompt configured for stage {stage!r}")

    data = _read_json(prompt_dir() / f"{selected}.json")
    if data.get("id") != selected:
        raise PromptCatalogError(f"prompt id mismatch in {selected}.json")
    if data.get("stage") != stage:
        raise PromptCatalogError(
            f"prompt {selected!r} belongs to stage {data.get('stage')!r}, not {stage!r}"
        )
    if not isinstance(data.get("system"), str) or not data["system"].strip():
        raise PromptCatalogError(f"prompt {selected!r} has no non-empty system prompt")
    return data
