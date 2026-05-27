from ..settings import Settings


_settings: Settings | None = None


def configure_settings(settings: Settings) -> None:
    global _settings
    _settings = settings


def current_settings() -> Settings:
    if _settings is None:
        raise RuntimeError(
            "settings not configured — call configure_settings() at startup"
        )
    return _settings
