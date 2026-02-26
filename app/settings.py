from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    db_path: Path


def get_settings() -> Settings:
    data_dir = Path.cwd() / ".data"
    return Settings(data_dir=data_dir, db_path=data_dir / "ledger.sqlite")
