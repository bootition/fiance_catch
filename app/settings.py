from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    db_path: Path


def get_settings() -> Settings:
    data_dir = PROJECT_ROOT / ".data"
    return Settings(data_dir=data_dir, db_path=data_dir / "ledger.sqlite")
