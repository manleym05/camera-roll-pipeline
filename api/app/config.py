from datetime import date
from pathlib import Path

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Home(BaseModel):
    """A place lived, valid from start (inclusive) to end (exclusive). Open-ended when None."""

    lat: float
    lon: float
    start: date | None = None
    end: date | None = None
    label: str = ""
    owner: str = "owner"

    def covers(self, day: date | None) -> bool:
        if day is None:
            return self.end is None  # undated media is judged against the current home
        return (self.start is None or day >= self.start) and (self.end is None or day < self.end)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore", env_ignore_empty=True)

    database_url: str = "postgresql+psycopg://brand:brand@localhost:5433/brand"
    agent_token: str = "change-me"

    # JSON list of Home. Several entries may share a period (e.g. two spots in one city).
    # Every home of every owner is geofenced; trips are measured from PRIMARY_OWNER's homes.
    homes: list[Home] = []
    primary_owner: str = "owner"
    # Display names for photo credits
    owner_names: dict[str, str] = {"owner": "the owner", "partner": "Partner"}
    # Words a caption must never contain, per platform. Useful when an employer,
    # client or project name belongs on one feed but not another.
    # e.g. INSTAGRAM_BLOCKED_TERMS='["acme corp"]'
    instagram_blocked_terms: list[str] = []
    x_blocked_terms: list[str] = []
    home_radius_m: float = 300
    home_trip_km: float = 100

    # Review page auth. "access": require a Cloudflare Access JWT for an allowlisted email.
    # "none": open (local dev only — the API must then listen on 127.0.0.1 only).
    review_auth: str = "access"
    cf_access_team_domain: str = ""  # e.g. "owner.cloudflareaccess.com"
    cf_access_aud: str = ""  # Access application AUD tag
    reviewer_emails: list[str] = ["you@example.com"]
    # Where the page loads thumbnails from: "" serves them from this API at /thumbs, or an R2 URL.
    thumb_base_url: str = ""
    # Origins allowed to call the API from a browser (the Pages site)
    cors_origins: list[str] = []


settings = Settings()
