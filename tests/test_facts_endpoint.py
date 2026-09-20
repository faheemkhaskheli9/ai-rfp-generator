def _create_requirement(client) -> int:
    response = client.post("/requirements", data={"text": "Build a customer portal"})
    assert response.status_code == 201
    return response.json()["id"]


def _upload_source_material(client, requirement_id: int, filename: str, content: bytes) -> None:
    response = client.post(
        f"/requirements/{requirement_id}/source-materials",
        files=[("files", (filename, content, "text/plain"))],
    )
    assert response.status_code == 201


def test_extract_facts_end_to_end(client):
    requirement_id = _create_requirement(client)
    _upload_source_material(
        client,
        requirement_id,
        "case_study.txt",
        b"We delivered a claims-processing system for a mid-size insurer in 2022.",
    )

    response = client.post(f"/requirements/{requirement_id}/facts")

    assert response.status_code == 201
    facts = response.json()["facts"]
    assert len(facts) == 1
    fact = facts[0]
    assert fact["requirement_id"] == requirement_id
    assert fact["is_duplicate"] is False
    assert fact["text"] == "We delivered a claims-processing system for a mid-size insurer in 2022."


def test_extract_facts_for_missing_requirement_returns_404(client):
    response = client.post("/requirements/999999/facts")
    assert response.status_code == 404


def test_extract_facts_without_source_materials_returns_422(client):
    requirement_id = _create_requirement(client)
    response = client.post(f"/requirements/{requirement_id}/facts")
    assert response.status_code == 422


def test_re_extracting_does_not_duplicate_facts_for_already_processed_material(client):
    requirement_id = _create_requirement(client)
    _upload_source_material(
        client, requirement_id, "case_study.txt", b"We support 24/7 customer service for all clients."
    )

    first = client.post(f"/requirements/{requirement_id}/facts")
    second = client.post(f"/requirements/{requirement_id}/facts")

    assert first.status_code == 201
    assert len(first.json()["facts"]) == 1
    assert second.status_code == 201
    assert second.json()["facts"] == []

    listing = client.get(f"/requirements/{requirement_id}/facts")
    assert listing.status_code == 200
    assert len(listing.json()["facts"]) == 1


def test_list_facts_for_missing_requirement_returns_404(client):
    response = client.get("/requirements/999999/facts")
    assert response.status_code == 404
