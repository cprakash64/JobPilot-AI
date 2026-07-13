import json
from functools import lru_cache
import os

from pydantic import Field, field_validator

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
except ModuleNotFoundError:
    from pydantic import BaseModel

    def SettingsConfigDict(**kwargs):
        return kwargs

    class BaseSettings(BaseModel):
        def __init__(self, **data):
            env_values = {
                name: os.environ[name.upper()]
                for name in self.__class__.model_fields
                if name.upper() in os.environ
            }
            env_values.update(data)
            super().__init__(**env_values)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./jobpilot.db"
    redis_url: str = "redis://localhost:6379/0"
    secret_key: str = Field(default="dev-only-change-me", min_length=12)
    jwt_expires_minutes: int = 60 * 24 * 7
    cors_origins: list[str] = ["http://localhost:3000"]
    app_env: str = "development"
    openai_api_key: str | None = None
    openai_model_smart: str = "gpt-5.5"
    openai_model_fast: str = "gpt-5-mini"
    openai_embedding_model: str = "text-embedding-3-small"
    demographics_encryption_key: str | None = None
    upload_dir: str = "uploads"
    run_migrations_on_startup: bool = False
    # Public ATS boards to query during discovery, as "provider:slug:Display Name"
    # entries (e.g. "greenhouse:stripe:Stripe"). Empty falls back to a curated
    # default registry. Only public/allowed endpoints are ever queried.
    job_source_companies: list[str] = []
    job_sources_file: str | None = None
    job_discovery_max_companies: int = 200
    job_discovery_max_jobs_per_source: int = 100
    job_discovery_concurrency: int = 10
    job_discovery_timeout_seconds: float = 12.0
    job_discovery_source_packs: list[str] = []
    job_discovery_include_unknown_dates: bool = False
    job_discovery_cache_ttl_minutes: int = 60

    # --- Daily automated ingestion (scheduler) ---
    job_ingestion_enabled: bool = True
    # Cron expression (m h dom mon dow). Default: once daily at 06:00.
    job_ingestion_schedule: str = "0 6 * * *"
    job_ingestion_timezone: str = "UTC"
    job_posted_within_days: int = 7
    job_ingestion_source_timeout_seconds: float = 60.0
    job_ingestion_max_retries: int = 3
    # Seconds a distributed ingestion lock is held before it is considered stale
    # and recoverable (must exceed a normal run's duration).
    job_ingestion_lock_ttl_seconds: int = 3600
    # A job unseen on its source for longer than this is marked inactive/expired.
    job_expiry_grace_days: int = 3

    # --- Background scoring ---
    job_scoring_batch_size: int = 100
    job_scoring_max_attempts: int = 3

    @field_validator("cors_origins", "job_source_companies", "job_discovery_source_packs", mode="before")
    @classmethod
    def parse_cors(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            if value.strip().startswith("["):
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return [str(item).strip() for item in parsed if str(item).strip()]
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
