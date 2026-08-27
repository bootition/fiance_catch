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
    assert "概览" in response.text
    assert "统计周期" in response.text


def test_index_shows_overview_metrics(v2_client):
    client, _ = v2_client
    response = client.get("/")
    assert "总消费" in response.text
    assert "总收入" in response.text
    assert "待确认" in response.text


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


def test_pages_render_after_production_init(tmp_path, monkeypatch):
    """生产 init_db 后全部新页面可访问（不 500）。"""
    settings = Settings(data_dir=tmp_path, db_path=tmp_path / "t.sqlite")
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    with TestClient(main.app) as client:
        for path in ("/imports/new", "/inbox", "/transactions", "/rules", "/imports"):
            response = client.get(path)
            assert response.status_code == 200, f"{path} -> {response.status_code}"


def test_index_ignores_invalid_ym(v2_client):
    """非法 ym 参数回退默认月，不 500（红队修复，2026-08-14）。"""
    client, _ = v2_client
    for bad in ("abc", "2026-13", "2026-7", "2026-99"):
        response = client.get("/", params={"ym": bad})
        assert response.status_code == 200, f"ym={bad} -> {response.status_code}"
