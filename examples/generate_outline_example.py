"""End-to-end demo: sample requirement text -> normalized items -> LLM outline.

Uses a fake outline-generator client so this runs with no API key/network
access — swap in ``ai_rfp_generator.outline.OpenAIOutlineClient(api_key)``
for a real call once ``OPENAI_API_KEY`` is set.

    PYTHONPATH=src python examples/generate_outline_example.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_rfp_generator.normalize import normalize_text
from ai_rfp_generator.outline import OutlineGeneratorClient, generate_outline

SAMPLE_REQUIREMENT = """\
Vendor Requirements
The platform must support Single Sign-On (SSO) via SAML.
Must comply with SOC 2 Type II.
What is your average incident response time?
Proposals are due by 2026-11-01.
"""


class DemoOutlineClient:
    """A canned client standing in for a real LLM call in this offline demo."""

    @property
    def model_name(self) -> str:
        return "demo-fake-model"

    def generate(self, requirement_text: str) -> list[dict]:
        return [
            {"title": "Security & Compliance", "description": "Address SSO/SAML support and SOC 2 Type II compliance."},
            {"title": "Support & SLAs", "description": "Cover incident response time and support commitments."},
            {"title": "Timeline", "description": "Confirm proposal submission deadline and delivery milestones."},
        ]


def main() -> None:
    items = normalize_text(SAMPLE_REQUIREMENT)
    print(f"parsed {len(items)} requirement items")

    # `generate_outline` only reads .position/.item_type/.content, which
    # `ParsedItem` already provides -- no need to persist to the DB first.
    client: OutlineGeneratorClient = DemoOutlineClient()
    draft = generate_outline(client, items)

    print(f"\ngenerated outline (model={draft.model}):")
    for section in draft.sections:
        print(f"  {section.position + 1}. {section.title} — {section.description}")


if __name__ == "__main__":
    main()
