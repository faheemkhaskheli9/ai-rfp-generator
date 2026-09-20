import importlib
import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A TestClient wired to a fresh throwaway SQLite DB and source-materials
    directory per test (never the repo's real ``./source_materials`` default).
    """
    db_path = tmp_path / "rfp_test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("SOURCE_MATERIALS_DIR", str(tmp_path / "source_materials"))

    import ai_rfp_generator.app as app_module

    importlib.reload(app_module)  # re-create engine/session factory against the new DB
    return TestClient(app_module.app)
