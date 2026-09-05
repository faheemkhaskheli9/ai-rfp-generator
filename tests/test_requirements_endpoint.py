from ai_rfp_generator.db import Requirement, make_session_factory


def test_text_submission_is_persisted_and_returns_id(client):
    response = client.post("/requirements", data={"text": "Build a customer portal"})
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "received"
    assert isinstance(body["id"], int)


def test_file_upload_txt_is_extracted_and_persisted(client):
    response = client.post(
        "/requirements",
        files={"file": ("requirements.txt", b"Must support SSO login", "text/plain")},
    )
    assert response.status_code == 201


def test_empty_text_submission_is_rejected(client):
    response = client.post("/requirements", data={"text": "   "})
    assert response.status_code == 400
    assert "empty" in response.json()["detail"]


def test_no_text_and_no_file_is_rejected(client):
    response = client.post("/requirements")
    assert response.status_code == 400


def test_unsupported_file_type_is_rejected(client):
    response = client.post(
        "/requirements",
        files={"file": ("scan.png", b"\x89PNG\r\n", "image/png")},
    )
    assert response.status_code == 400
    assert "unsupported" in response.json()["detail"].lower()


def test_persisted_requirement_is_readable_from_the_database(client, tmp_path):
    import os

    client.post("/requirements", data={"text": "Persisted content check"})

    from ai_rfp_generator.db import make_engine

    engine = make_engine(os.environ["DATABASE_URL"])
    session_factory = make_session_factory(engine)
    with session_factory() as session:
        rows = session.query(Requirement).all()
        assert len(rows) == 1
        assert rows[0].content == "Persisted content check"
        assert rows[0].status == "received"
