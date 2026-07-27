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
    # Validated at startup by app.core.config_validation: when app_env names a
    # production environment, development defaults (the shipped SECRET_KEY, the
    # compose database password, wildcard CORS, DEBUG) are refused outright.
    debug: bool = False
    # Whether CORS responses may carry credentials — paired with cors_origins by
    # the wildcard check, since "*" plus credentials is both unsafe and broken.
    cors_allow_credentials: bool = True
    # Set true once demographics are stored encrypted; makes the encryption key
    # mandatory in production rather than optional.
    demographics_encryption_required: bool = False
    # None = follow app_env (docs served outside production only).
    docs_enabled: bool | None = None
    openai_api_key: str | None = None
    # Role-preserving GPT-5.6 defaults: Sol for quality-first document
    # generation, Terra for the lower-latency/price path. Environment overrides
    # remain supported for deployments with pinned models.
    openai_model_smart: str = "gpt-5.6-sol"
    openai_model_fast: str = "gpt-5.6-terra"
    openai_embedding_model: str = "text-embedding-3-small"
    demographics_encryption_key: str | None = None
    # Separate key for encrypted employer-account credentials. Development may
    # derive from SECRET_KEY; production validation requires this dedicated key.
    workday_credentials_encryption_key: str | None = None
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
    # Keep back-to-back searches efficient without hiding newly opened roles for
    # most of an hour after a user explicitly asks for fresh jobs.
    job_discovery_cache_ttl_minutes: int = 15

    # --- Daily automated ingestion (scheduler) ---
    job_ingestion_enabled: bool = True
    # Cron expression (m h dom mon dow). The default performs one authoritative
    # refresh every 24 hours; interactive discovery can still be run on demand.
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

    # --- People Who Can Help (all controls fail closed) ---
    people_recommendations_enabled: bool = False
    people_rollout_mode: str = "disabled"  # disabled|internal|beta|percentage|all
    people_rollout_percentage: int = 0
    people_internal_emails: list[str] = []
    people_beta_user_ids: list[str] = []
    people_primary_provider: str = "apollo"
    people_email_discovery_enabled: bool = False
    people_pdl_fallback_enabled: bool = False
    people_outreach_drafting_enabled: bool = False
    people_network_matching_enabled: bool = False
    people_employment_secondary_verification_enabled: bool = False
    people_employment_comparison_mode: bool = False
    apollo_api_key: str | None = None
    hunter_api_key: str | None = None
    pdl_api_key: str | None = None
    people_data_encryption_key: str | None = None
    people_result_ttl_days: int = 30
    people_employment_freshness_days: int = 180
    people_email_result_ttl_days: int = 30
    people_max_discovery_results_per_category: int = 20
    people_max_displayed_recruiters: int = 2
    people_max_displayed_managers: int = 2
    people_max_displayed_referrers: int = 5
    people_max_enrichments_per_job: int = 8
    people_daily_credit_budget: int = 0
    people_per_user_daily_limit: int = 0
    people_email_daily_credit_budget: int = 0
    people_email_per_user_daily_limit: int = 0
    people_provider_timeout_seconds: float = 8.0
    people_provider_response_max_bytes: int = 1_000_000
    people_min_relevance_score: float = 60.0
    people_min_recruiter_relevance: float = 60.0
    people_min_manager_relevance: float = 60.0
    people_min_referrer_relevance: float = 60.0
    people_min_data_confidence: float = 0.5
    people_recruiter_enrichment_reserve: int = 3
    people_manager_enrichment_reserve: int = 3
    people_referrer_enrichment_reserve: int = 2
    people_discovery_rate_limit_per_hour: int = 10
    people_email_rate_limit_per_hour: int = 10

    @field_validator(
        "cors_origins",
        "job_source_companies",
        "job_discovery_source_packs",
        "people_internal_emails",
        "people_beta_user_ids",
        mode="before",
    )
    @classmethod
    def parse_cors(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            if value.strip().startswith("["):
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return [str(item).strip() for item in parsed if str(item).strip()]
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


    def docs_are_enabled(self) -> bool:
        """Serve /docs, /redoc and the OpenAPI schema?

        Explicit DOCS_ENABLED wins; otherwise docs are on everywhere except a
        production environment. The schema enumerates every endpoint and payload
        shape, which is free reconnaissance in production.
        """
        if self.docs_enabled is not None:
            return self.docs_enabled
        from app.core.config_validation import is_production

        return not is_production(self.app_env)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
