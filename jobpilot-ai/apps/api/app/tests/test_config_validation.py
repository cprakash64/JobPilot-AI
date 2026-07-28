"""Section G — production configuration must fail closed, and never echo values.

Every unsafe setting checked here has a working development default, so a
misconfigured production deploy boots cleanly and looks healthy. Signing JWTs
with the shipped `SECRET_KEY` lets anyone forge a token for any user, and
nothing about the running system reveals it.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from app.core.config_validation import (
    ConfigurationError,
    collect_findings,
    enforce,
    is_production,
)
from app.core.log_redaction import RedactingFilter, redact

STRONG_KEY = "Zq7pR2vK9wX4mB6nT1yH8jL5sD3fG0aC"
SAFE_DB = "postgresql+psycopg://jobpilot:F9x2Qv7LmR4t@db.internal:5432/jobpilot"


def make_settings(**overrides):
    base = dict(
        app_env="production",
        debug=False,
        secret_key=STRONG_KEY,
        demographics_encryption_key="k" * 32,
        demographics_encryption_required=False,
        workday_credentials_encryption_key="w" * 32,
        people_email_discovery_enabled=False,
        cors_origins=["https://app.jobpilot.example"],
        cors_allow_credentials=True,
        database_url=SAFE_DB,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# --------------------------------------------------------------------------- #
# Environment gating — dev keeps its conveniences
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("env", ["production", "prod", "staging", "PRODUCTION"])
def test_production_like_environments_are_validated(env: str) -> None:
    assert is_production(env) is True


@pytest.mark.parametrize("env", ["development", "dev", "test", "local", "ci", "", None])
def test_non_production_environments_are_not_validated(env) -> None:
    assert is_production(env) is False


def test_development_defaults_are_allowed_in_development() -> None:
    """Requirement: dev defaults stay usable when an explicit development
    environment is selected."""
    dev = make_settings(
        app_env="development",
        secret_key="dev-only-change-me",
        database_url="postgresql+psycopg://jobpilot:jobpilot_dev_password@localhost:5432/jobpilot",
        cors_origins=["*"],
        debug=True,
    )
    enforce(dev)  # must not raise


def test_a_valid_production_config_starts() -> None:
    assert collect_findings(make_settings()) == []
    enforce(make_settings())


# --------------------------------------------------------------------------- #
# SECRET_KEY
# --------------------------------------------------------------------------- #
def test_the_documented_default_secret_key_is_rejected() -> None:
    findings = collect_findings(make_settings(secret_key="dev-only-change-me"))
    assert any(f.setting == "SECRET_KEY" for f in findings)


@pytest.mark.parametrize("key", ["", None, "   "])
def test_a_missing_secret_key_is_rejected(key) -> None:
    assert any(f.setting == "SECRET_KEY" for f in collect_findings(make_settings(secret_key=key)))


def test_a_short_secret_key_is_rejected() -> None:
    assert any(
        f.setting == "SECRET_KEY" for f in collect_findings(make_settings(secret_key="abc123"))
    )


def test_a_long_but_low_entropy_secret_key_is_rejected() -> None:
    """Padding to the length bar with one repeated character is not a key."""
    assert any(
        f.setting == "SECRET_KEY" for f in collect_findings(make_settings(secret_key="a" * 64))
    )


# --------------------------------------------------------------------------- #
# Demographics encryption key
# --------------------------------------------------------------------------- #
def test_encryption_key_is_required_only_when_encryption_is_enabled() -> None:
    off = collect_findings(
        make_settings(demographics_encryption_key=None, demographics_encryption_required=False)
    )
    assert not any(f.setting == "DEMOGRAPHICS_ENCRYPTION_KEY" for f in off)

    on = collect_findings(
        make_settings(demographics_encryption_key=None, demographics_encryption_required=True)
    )
    assert any(f.setting == "DEMOGRAPHICS_ENCRYPTION_KEY" for f in on)


def test_workday_credentials_use_a_dedicated_production_key() -> None:
    findings = collect_findings(make_settings(workday_credentials_encryption_key=None))
    assert any(f.setting == "WORKDAY_CREDENTIALS_ENCRYPTION_KEY" for f in findings)


def test_people_email_requires_separate_provider_and_positive_budgets() -> None:
    findings = collect_findings(make_settings(
        people_email_discovery_enabled=True,
        people_data_encryption_key="e" * 32,
        hunter_api_key=None,
        people_email_daily_credit_budget=0,
        people_email_per_user_daily_limit=0,
        people_email_result_ttl_days=0,
    ))
    names = {finding.setting for finding in findings}
    assert {
        "HUNTER_API_KEY",
        "PEOPLE_EMAIL_DAILY_CREDIT_BUDGET",
        "PEOPLE_EMAIL_PER_USER_DAILY_LIMIT",
        "PEOPLE_EMAIL_RESULT_TTL_DAYS",
    } <= names


def test_people_email_configuration_accepts_separate_positive_limits() -> None:
    findings = collect_findings(make_settings(
        people_email_discovery_enabled=True,
        people_data_encryption_key="e" * 32,
        hunter_api_key="configured-secret",
        people_email_daily_credit_budget=100,
        people_email_per_user_daily_limit=5,
        people_email_result_ttl_days=30,
    ))
    assert not any(
        finding.setting.startswith("PEOPLE_EMAIL")
        or finding.setting == "HUNTER_API_KEY"
        for finding in findings
    )


def test_secondary_employment_verification_requires_provider_and_own_budgets() -> None:
    findings = collect_findings(make_settings(
        people_employment_secondary_verification_enabled=True,
        pdl_api_key=None,
        people_employment_verification_daily_credit_budget=0,
        people_employment_verification_per_user_daily_limit=0,
    ))
    names = {finding.setting for finding in findings}
    assert {
        "PDL_API_KEY",
        "PEOPLE_EMPLOYMENT_VERIFICATION_DAILY_CREDIT_BUDGET",
        "PEOPLE_EMPLOYMENT_VERIFICATION_PER_USER_DAILY_LIMIT",
    } <= names


def test_secondary_employment_verification_accepts_separate_positive_limits() -> None:
    findings = collect_findings(make_settings(
        people_employment_secondary_verification_enabled=True,
        pdl_api_key="configured-secret",
        people_employment_verification_daily_credit_budget=30,
        people_employment_verification_per_user_daily_limit=3,
    ))
    assert not any(
        finding.setting == "PDL_API_KEY"
        or finding.setting.startswith("PEOPLE_EMPLOYMENT_VERIFICATION")
        for finding in findings
    )


def test_enabled_people_discovery_requires_pdl_and_independent_limits() -> None:
    findings = collect_findings(
        make_settings(
            people_recommendations_enabled=True,
            people_primary_provider="apollo",
        )
    )
    assert any(
        finding.setting == "PEOPLE_PRIMARY_PROVIDER"
        for finding in findings
    )

    findings = collect_findings(
        make_settings(
            people_recommendations_enabled=True,
            people_primary_provider="pdl",
            people_pdl_discovery_enabled=True,
            pdl_api_key=None,
            people_pdl_daily_credit_budget=0,
            people_pdl_per_user_daily_limit=0,
            people_pdl_result_ttl_days=0,
        )
    )
    names = {finding.setting for finding in findings}
    assert {
        "PDL_API_KEY",
        "PEOPLE_PDL_DAILY_CREDIT_BUDGET",
        "PEOPLE_PDL_PER_USER_DAILY_LIMIT",
        "PEOPLE_PDL_RESULT_TTL_DAYS",
    } <= names


def test_enabled_pdl_discovery_accepts_independent_limits() -> None:
    findings = collect_findings(
        make_settings(
            people_recommendations_enabled=True,
            people_primary_provider="pdl",
            people_pdl_discovery_enabled=True,
            pdl_api_key="configured-secret",
            people_pdl_daily_credit_budget=50,
            people_pdl_per_user_daily_limit=5,
            people_pdl_result_ttl_days=30,
        )
    )
    assert not any(
        finding.setting.startswith("PEOPLE_PDL")
        or finding.setting in {"PDL_API_KEY", "PEOPLE_PRIMARY_PROVIDER"}
        for finding in findings
    )


# --------------------------------------------------------------------------- #
# CORS
# --------------------------------------------------------------------------- #
def test_wildcard_cors_with_credentials_is_rejected() -> None:
    findings = collect_findings(make_settings(cors_origins=["*"], cors_allow_credentials=True))
    assert any(f.setting == "CORS_ORIGINS" and "credential" in f.problem for f in findings)


def test_wildcard_cors_without_credentials_is_still_rejected_in_production() -> None:
    findings = collect_findings(make_settings(cors_origins=["*"], cors_allow_credentials=False))
    assert any(f.setting == "CORS_ORIGINS" for f in findings)


def test_empty_cors_origins_are_rejected() -> None:
    assert any(
        f.setting == "CORS_ORIGINS" for f in collect_findings(make_settings(cors_origins=[]))
    )


def test_plaintext_http_origin_is_rejected_but_localhost_is_allowed() -> None:
    assert any(
        f.setting == "CORS_ORIGINS"
        for f in collect_findings(make_settings(cors_origins=["http://app.example"]))
    )
    assert not any(
        f.setting == "CORS_ORIGINS"
        for f in collect_findings(make_settings(cors_origins=["http://localhost:3000"]))
    )


# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #
def test_the_compose_development_database_password_is_rejected() -> None:
    url = "postgresql+psycopg://jobpilot:jobpilot_dev_password@db:5432/jobpilot"
    assert any(
        f.setting == "DATABASE_URL" for f in collect_findings(make_settings(database_url=url))
    )


def test_a_passwordless_database_url_is_rejected() -> None:
    url = "postgresql+psycopg://jobpilot@db:5432/jobpilot"
    assert any(
        f.setting == "DATABASE_URL" for f in collect_findings(make_settings(database_url=url))
    )


def test_sqlite_is_rejected_in_production() -> None:
    assert any(
        f.setting == "DATABASE_URL"
        for f in collect_findings(make_settings(database_url="sqlite:///./x.db"))
    )


def test_debug_enabled_is_rejected() -> None:
    assert any(f.setting == "DEBUG" for f in collect_findings(make_settings(debug=True)))


# --------------------------------------------------------------------------- #
# The error must not leak the values it rejected
# --------------------------------------------------------------------------- #
def test_the_startup_error_names_settings_but_never_prints_values() -> None:
    bad = make_settings(
        secret_key="hunter2-actual-secret-value",
        database_url="postgresql+psycopg://jobpilot:jobpilot_dev_password@db:5432/jobpilot",
        debug=True,
    )
    with pytest.raises(ConfigurationError) as exc:
        enforce(bad)

    message = str(exc.value)
    assert "SECRET_KEY" in message and "DATABASE_URL" in message and "DEBUG" in message
    assert "hunter2-actual-secret-value" not in message
    assert "jobpilot_dev_password" not in message


def test_enforce_reports_every_problem_at_once() -> None:
    """One restart should reveal the whole list, not the first item repeatedly."""
    bad = make_settings(secret_key="short", cors_origins=[], debug=True)
    with pytest.raises(ConfigurationError) as exc:
        enforce(bad)
    message = str(exc.value)
    assert all(name in message for name in ("SECRET_KEY", "CORS_ORIGINS", "DEBUG"))


# --------------------------------------------------------------------------- #
# Log redaction
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "text,secret",
    [
        ("connecting to postgresql://jobpilot:sup3rs3cret@db:5432/x", "sup3rs3cret"),
        ("redis://default:c4ch3p4ss@cache:6379/0 failed", "c4ch3p4ss"),
        ("SECRET_KEY=Zq7pR2vK9wX4mB6nT1yH8jL5sD3fG0aC loaded", "Zq7pR2vK9wX4mB6nT1yH8jL5sD3fG0aC"),
        ("password: hunter2extra", "hunter2extra"),
        ("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abc", "eyJhbGciOiJIUzI1NiJ9.abc"),
        ("using sk-abcdefghijklmnopqrstuvwxyz123456", "sk-abcdefghijklmnopqrstuvwxyz123456"),
        ("token=ghp_abcdefghijklmnopqrstuvwxyz1234", "ghp_abcdefghijklmnopqrstuvwxyz1234"),
    ],
)
def test_credential_shapes_are_redacted(text: str, secret: str) -> None:
    out = redact(text)
    assert secret not in out
    assert "***REDACTED***" in out


def test_redaction_keeps_the_useful_context() -> None:
    out = redact("connecting to postgresql://jobpilot:sup3rs3cret@db.internal:5432/jobpilot")
    # Host and database survive so the log line is still diagnosable.
    assert "db.internal" in out and "jobpilot" in out


def test_redaction_leaves_ordinary_messages_untouched() -> None:
    message = "Scored 12 jobs for user 7 in 1.4s"
    assert redact(message) == message


def test_the_filter_redacts_lazily_formatted_log_arguments() -> None:
    record = logging.LogRecord(
        name="t",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="db url=%s",
        args=("postgresql://u:t0psecret@h/db",),
        exc_info=None,
    )
    RedactingFilter().filter(record)
    assert "t0psecret" not in record.getMessage()
