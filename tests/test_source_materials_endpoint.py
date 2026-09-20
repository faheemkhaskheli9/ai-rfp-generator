def _create_requirement(client) -> int:
    response = client.post("/requirements", data={"text": "Build a customer portal"})
    assert response.status_code == 201
    return response.json()["id"]


def test_upload_source_materials_end_to_end(client):
    requirement_id = _create_requirement(client)

    response = client.post(
        f"/requirements/{requirement_id}/source-materials",
        files=[
            ("files", ("past_proposal.txt", b"Delivered a similar portal in 2024.", "text/plain")),
        ],
    )

    assert response.status_code == 201
    body = response.json()
    assert len(body["source_materials"]) == 1
    material = body["source_materials"][0]
    assert material["requirement_id"] == requirement_id
    assert material["original_filename"] == "past_proposal.txt"
    assert material["extension"] == ".txt"
    assert material["size_bytes"] == len(b"Delivered a similar portal in 2024.")
    assert material["content_hash"]


def test_upload_multiple_source_materials_in_one_request(client):
    requirement_id = _create_requirement(client)

    response = client.post(
        f"/requirements/{requirement_id}/source-materials",
        files=[
            ("files", ("case_study_a.txt", b"Case study A", "text/plain")),
            ("files", ("case_study_b.txt", b"Case study B", "text/plain")),
        ],
    )

    assert response.status_code == 201
    filenames = {m["original_filename"] for m in response.json()["source_materials"]}
    assert filenames == {"case_study_a.txt", "case_study_b.txt"}


def test_upload_source_materials_for_missing_requirement_returns_404(client):
    response = client.post(
        "/requirements/999999/source-materials",
        files=[("files", ("a.txt", b"content", "text/plain"))],
    )
    assert response.status_code == 404


def test_unsupported_file_type_is_rejected(client):
    requirement_id = _create_requirement(client)

    response = client.post(
        f"/requirements/{requirement_id}/source-materials",
        files=[("files", ("scan.png", b"\x89PNG\r\n", "image/png"))],
    )

    assert response.status_code == 400
    assert "unsupported" in response.json()["detail"].lower()


def test_one_bad_file_in_batch_rejects_the_whole_request(client):
    requirement_id = _create_requirement(client)

    response = client.post(
        f"/requirements/{requirement_id}/source-materials",
        files=[
            ("files", ("ok.txt", b"fine content", "text/plain")),
            ("files", ("scan.png", b"\x89PNG\r\n", "image/png")),
        ],
    )
    assert response.status_code == 400

    # Nothing from the rejected batch should have been persisted.
    follow_up = client.post(
        f"/requirements/{requirement_id}/source-materials",
        files=[("files", ("ok.txt", b"fine content", "text/plain"))],
    )
    assert follow_up.status_code == 201
    assert len(follow_up.json()["source_materials"]) == 1


def test_reuploading_identical_file_is_idempotent(client):
    requirement_id = _create_requirement(client)
    raw = b"Identical capability statement content."

    first = client.post(
        f"/requirements/{requirement_id}/source-materials",
        files=[("files", ("cap.txt", raw, "text/plain"))],
    )
    second = client.post(
        f"/requirements/{requirement_id}/source-materials",
        files=[("files", ("cap_renamed.txt", raw, "text/plain"))],
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["source_materials"][0]["id"] == second.json()["source_materials"][0]["id"]


def test_empty_file_is_rejected(client):
    requirement_id = _create_requirement(client)

    response = client.post(
        f"/requirements/{requirement_id}/source-materials",
        files=[("files", ("blank.txt", b"", "text/plain"))],
    )
    assert response.status_code == 400
    assert "empty" in response.json()["detail"].lower()
