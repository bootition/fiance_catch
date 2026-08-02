import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.settings import Settings


@pytest.fixture
def v2_client(tmp_path, monkeypatch):
    """以生产 app.main + 生产 init_db() 初始化全新 v2 库。"""
    settings = Settings(data_dir=tmp_path, db_path=tmp_path / "t.sqlite")
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    with TestClient(main.app) as client:
        yield client, settings


def test_index_returns_200_after_production_migration(v2_client):
    client, _ = v2_client
    response = client.get("/")
    assert response.status_code == 200
    assert "重构" in response.text or "迁移" in response.text


def test_index_shows_v2_schema_status(v2_client):
    client, _ = v2_client
    response = client.get("/")
    assert "ledger_entries" in response.text
    assert "source_transactions" in response.text
    assert "v2 已初始化" in response.text


def test_legacy_routes_disabled_after_migration(v2_client):
    client, _ = v2_client
    for path in ("/review", "/cleanup"):
        assert client.get(path).status_code == 404


def test_index_ok_on_rerun_migrated_db(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, db_path=tmp_path / "t.sqlite")
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    with TestClient(main.app) as client:
        assert client.get("/").status_code == 200
    with TestClient(main.app) as client:
        assert client.get("/").status_code == 200
