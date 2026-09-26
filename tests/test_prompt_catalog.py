"""Tests for configuration-driven prompt catalog."""

from __future__ import annotations

import json
from types import SimpleNamespace

from ai_rfp_generator.outline import OpenAIOutlineClient
from ai_rfp_generator.prompts import active_prompt_ids, load_prompt


class _ChatCompletions:
    def __init__(self):
        self.messages = None

    def create(self, **kwargs):
        self.messages = kwargs["messages"]
        payload = json.dumps(
            {"sections": [{"title": "Custom", "description": "Loaded from config."}]}
        )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=payload))]
        )


class _FakeOpenAI:
    def __init__(self):
        self.chat = SimpleNamespace(completions=_ChatCompletions())


def _write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def test_active_prompt_catalog_loads_checked_in_versions():
    active = active_prompt_ids()

    assert active["outline"] == "outline-v1"
    assert active["drafting"] == "drafting-v1"
    assert active["validation"] == "validation-v1"
    assert load_prompt("outline")["id"] == "outline-v1"


def test_switching_active_prompt_requires_no_code_change(tmp_path, monkeypatch):
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()

    _write(prompt_dir / "active.json", {"outline": "outline-v2"})
    _write(
        prompt_dir / "outline-v2.json",
        {
            "id": "outline-v2",
            "stage": "outline",
            "version": 2,
            "system": "CUSTOM V2 OUTLINE INSTRUCTION",
        },
    )
    monkeypatch.setenv("PROMPT_CONFIG_DIR", str(prompt_dir))

    fake = _FakeOpenAI()
    client = OpenAIOutlineClient("unused", client=fake)
    output = client.generate("[requirement] Explain security.")

    assert output[0]["title"] == "Custom"
    assert fake.chat.completions.messages[0]["content"] == "CUSTOM V2 OUTLINE INSTRUCTION"
