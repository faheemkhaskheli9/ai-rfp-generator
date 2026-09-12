"""Tests for POST /requirements/{id}/outline (issue #3).

A fake client stands in for ``OpenAIOutlineClient`` -- no network access or
API key is used to exercise the endpoint.
"""

from __future__ import annotations

import ai_rfp_generator.app as app_module


class _FakeClient:
    def __init__(self, api_key, *, model=None, client=None):
        self.model_name_value = model or "fake-outline-model"

    @property
    def model_name(self) -> str:
        return self.model_name_value

    def generate(self, requirement_text: str):
        return [{"title": "Security", "description": "Describe SSO support."}]


class _EmptyClient(_FakeClient):
    def generate(self, requirement_text: str):
        return []


def test_outline_requires_openai_api_key(client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    submission = client.post("/requirements", data={"text": "Must support SSO"})
    requirement_id = submission.json()["id"]

    response = client.post(f"/requirements/{requirement_id}/outline")
    assert response.status_code == 503


def test_outline_generated_end_to_end_with_fake_client(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key-for-test")
    monkeypatch.setattr(app_module, "OpenAIOutlineClient", _FakeClient)

    submission = client.post("/requirements", data={"text": "Must support SSO"})
    requirement_id = submission.json()["id"]

    response = client.post(f"/requirements/{requirement_id}/outline")
    assert response.status_code == 201
    body = response.json()
    assert body["requirement_id"] == requirement_id
    assert body["model"] == "fake-outline-model"
    assert body["sections"] == [
        {"position": 0, "title": "Security", "description": "Describe SSO support."}
    ]


def test_outline_missing_requirement_is_404(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key-for-test")
    monkeypatch.setattr(app_module, "OpenAIOutlineClient", _FakeClient)

    response = client.post("/requirements/999/outline")
    assert response.status_code == 404


def test_outline_generation_failure_is_502(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key-for-test")
    monkeypatch.setattr(app_module, "OpenAIOutlineClient", _EmptyClient)

    submission = client.post("/requirements", data={"text": "Must support SSO"})
    requirement_id = submission.json()["id"]

    response = client.post(f"/requirements/{requirement_id}/outline")
    assert response.status_code == 502


def test_outline_persisted_and_queryable(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key-for-test")
    monkeypatch.setattr(app_module, "OpenAIOutlineClient", _FakeClient)

    submission = client.post("/requirements", data={"text": "Must support SSO"})
    requirement_id = submission.json()["id"]
    client.post(f"/requirements/{requirement_id}/outline")

    from ai_rfp_generator.db import Requirement, make_session_factory

    with app_module._SessionFactory() as session:
        requirement = session.get(Requirement, requirement_id)
        assert len(requirement.outlines) == 1
        assert requirement.outlines[0].sections[0].title == "Security"
