import importlib
from pathlib import Path

from starlette.routing import Mount

from app.settings import get_settings


def test_get_settings_uses_project_root_not_current_working_directory(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)

    settings = get_settings()

    assert settings.data_dir == Path(__file__).resolve().parents[1] / ".data"
    assert settings.db_path == settings.data_dir / "ledger.sqlite"


def test_static_files_mount_uses_project_root_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    import app.main as main

    reloaded_main = importlib.reload(main)
    static_mount = next(
        route
        for route in reloaded_main.app.routes
        if isinstance(route, Mount) and route.name == "static"
    )

    assert static_mount.app.all_directories == [
        str(Path(__file__).resolve().parents[1] / "static")
    ]
