from __future__ import annotations

import hashlib

from app.core.config import Settings, settings
from app.models.entities import User


def recommendations_availability(user: User, config: Settings = settings) -> str:
    """Safe product-facing availability state; never includes cohort/config values."""
    if not config.people_recommendations_enabled:
        return "globally_disabled"
    mode = config.people_rollout_mode.lower()
    if mode == "all":
        return "available"
    if mode == "internal":
        return (
            "available"
            if user.email.lower() in {v.lower() for v in config.people_internal_emails}
            else "not_in_rollout"
        )
    if mode == "beta":
        return "available" if str(user.id) in set(config.people_beta_user_ids) else "not_in_rollout"
    if mode == "percentage":
        bucket = int(hashlib.sha256(f"people:{user.id}".encode()).hexdigest()[:8], 16) % 100
        return (
            "available"
            if bucket < max(0, min(100, config.people_rollout_percentage))
            else "not_in_rollout"
        )
    return "configuration_unavailable"


def recommendations_enabled(user: User, config: Settings = settings) -> bool:
    return recommendations_availability(user, config) == "available"


def is_beta(user: User, config: Settings = settings) -> bool:
    return config.people_rollout_mode.lower() in {"internal", "beta", "percentage"}


def configuration_summary(config: Settings = settings) -> dict[str, bool | str]:
    provider = config.people_primary_provider.lower()
    provider_configured = (
        bool(
            config.apollo_api_key
            and config.people_apollo_discovery_enabled
            and config.people_apollo_diagnostic_enabled
            and config.people_rollout_mode.lower() == "internal"
        )
        if provider == "apollo"
        else bool(
            config.pdl_api_key
            and config.people_pdl_discovery_enabled
        )
        if provider == "pdl"
        else config.app_env in {"development", "test"}
        if provider == "mock"
        else False
    )
    return {
        "recommendations_enabled": config.people_recommendations_enabled,
        "email_discovery_enabled": config.people_email_discovery_enabled,
        "primary_provider_configured": provider_configured,
        "encryption_key_configured": bool(config.people_data_encryption_key),
        "global_budget_configured": (
            config.people_pdl_daily_credit_budget > 0
            if provider == "pdl"
            else config.people_daily_credit_budget > 0
        ),
        "per_user_budget_configured": (
            config.people_pdl_per_user_daily_limit > 0
            if provider == "pdl"
            else config.people_per_user_daily_limit > 0
        ),
        "rollout_mode": config.people_rollout_mode.lower(),
        "environment": config.app_env,
    }
