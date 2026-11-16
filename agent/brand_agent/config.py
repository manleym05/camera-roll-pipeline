from datetime import date
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore", env_ignore_empty=True)

    api_url: str = "http://localhost:8000"
    agent_token: str = "change-me"
    photos_dir: Path = Path.home() / "Pictures" / "iCloud Photos" / "Photos"
    # Other people's camera rolls, e.g. {"partner": "C:/Users/you/Pictures/Partner"} (forward slashes)
    owner_dirs: dict[str, Path] = {}
    ingest_since: date = date(2024, 1, 1)

    storage: str = "local"  # local | r2
    local_storage_dir: Path = REPO_ROOT / "data" / "storage"
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = "brand-media"

    clip_device: str = "auto"
    # CLIP document/screen probability above which a still is excluded. Off by default: on the
    # real library it flagged a gym selfie, a meal and an InBody scan. The score is always stored
    # in tags["doc"] so ranking can weigh it instead.
    doc_threshold: float | None = None
    cache_path: Path = REPO_ROOT / "data" / "agent-cache.sqlite3"


settings = Settings()
