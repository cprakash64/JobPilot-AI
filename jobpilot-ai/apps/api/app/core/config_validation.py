"""Fail-fast validation of production configuration.

The failure mode this prevents is silent: every unsafe setting here has a
*working* development default, so a misconfigured production deploy starts
cleanly, serves traffic, and is indistinguishable from a correct one until it is
exploited. Signing JWTs with the shipped default `SECRET_KEY` lets anyone forge
a token for any user.

So these checks run at startup and REFUSE to boot rather than warn. They apply
only when ``APP_ENV`` selects a production-like environment; development keeps
its convenient defaults (that is the whole point of having them).

Nothing in this module logs, returns or embeds a secret VALUE — findings name
the setting only, because the resulting message travels into logs and crash
reporters.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urlparse

logger = logging.getLogger("jobpilot.config")

# Environments that must be locked down. Everything else (development, test,
# local, ci) keeps the permissive defaults.
PRODUCTION_ENVS = frozenset({"production", "prod", "staging"})

# Values shipped in this repo's docs/compose/defaults. Anything matching is, by
# definition, public knowledge and unusable as a real secret.
KNOWN_DEV_SECRET_KEYS = frozenset(
    {
        "dev-only-change-me",
        "change-me",
        "changeme",
        "secret",
        "secret-key",
        "dev",
        "development",
        "test",
        "jobpilot",
        "please-change-this",
        "your-secret-key-here",
    }
)

KNOWN_DEV_DB_PASSWORDS = frozenset(
    {
        "jobpilot_dev_password",
        "jobpilot",
        "postgres",
        "password",
        "passwd",
        "admin",
        "root",
        "changeme",
        "change-me",
        "dev",
        "test",
    }
)

# A signing key shorter than this cannot carry 128 bits of entropy even if it is
# perfectly random.
MIN_SECRET_KEY_LENGTH = 32
# Guards against "aaaaaaaa..." or "01010101..." padding to reach the length bar.
MIN_SECRET_KEY_DISTINCT_CHARS = 8


class ConfigurationError(RuntimeError):
    """Raised when production configuration is unsafe. Never carries a value."""


@dataclass(frozen=True)
class Finding:
    setting: str
    problem: str

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.setting}: {self.problem}"


def is_production(app_env: str | None) -> bool:
    return (app_env or "").strip().lower() in PRODUCTION_ENVS


def _check_secret_key(secret_key: str | None) -> list[Finding]:
    key = (secret_key or "").strip()
    if not key:
        return [Finding("SECRET_KEY", "is missing")]
    if key.lower() in KNOWN_DEV_SECRET_KEYS:
        return [Finding("SECRET_KEY", "is a documented development default and is publicly known")]
    findings: list[Finding] = []
    if len(key) < MIN_SECRET_KEY_LENGTH:
        findings.append(
            Finding("SECRET_KEY", f"is shorter than {MIN_SECRET_KEY_LENGTH} characters")
        )
    if len(set(key)) < MIN_SECRET_KEY_DISTINCT_CHARS:
        findings.append(Finding("SECRET_KEY", "has too little variation to be a random key"))
    return findings


def _check_demographics_key(key: str | None, *, required: bool) -> list[Finding]:
    if not required:
        return []
    if not (key or "").strip():
        return [
            Finding(
                "DEMOGRAPHICS_ENCRYPTION_KEY",
                "is missing while encrypted demographics storage is enabled",
            )
        ]
    return []


def _check_cors(origins: list[str] | None, *, allow_credentials: bool) -> list[Finding]:
    values = [o.strip() for o in (origins or []) if o and o.strip()]
    if not values:
        return [
            Finding("CORS_ORIGINS", "is empty; production must list trusted origins explicitly")
        ]
    if "*" in values:
        if allow_credentials:
            # Browsers reject this combination outright, so the practical effect
            # is that every credentialed cross-origin call silently breaks — and
            # it signals the origin list was never configured.
            return [
                Finding(
                    "CORS_ORIGINS",
                    "uses the wildcard '*' together with credentialed requests",
                )
            ]
        return [Finding("CORS_ORIGINS", "uses the wildcard '*' in production")]
    findings = []
    for origin in values:
        if origin.startswith("http://") and not origin.startswith("http://localhost"):
            findings.append(
                Finding(
                    "CORS_ORIGINS", f"contains a plaintext http:// origin ({_host_only(origin)})"
                )
            )
    return findings


def _host_only(origin: str) -> str:
    """Host without scheme/path — safe to log, and never a credential."""
    try:
        return urlparse(origin).hostname or "unknown"
    except ValueError:
        return "unparseable"


def _check_database_url(database_url: str | None) -> list[Finding]:
    url = (database_url or "").strip()
    if not url:
        return [Finding("DATABASE_URL", "is missing")]
    if url.startswith("sqlite"):
        return [Finding("DATABASE_URL", "points at SQLite, which is not supported in production")]
    try:
        parsed = urlparse(url)
    except ValueError:
        return [Finding("DATABASE_URL", "could not be parsed")]

    findings: list[Finding] = []
    password = parsed.password
    # Compared, never logged or echoed.
    if password is None or password == "":
        findings.append(Finding("DATABASE_URL", "has no password"))
    elif password.lower() in KNOWN_DEV_DB_PASSWORDS:
        findings.append(Finding("DATABASE_URL", "uses a documented development database password"))
    return findings


def _check_debug(debug: bool) -> list[Finding]:
    return [Finding("DEBUG", "is enabled in production")] if debug else []


def collect_findings(settings) -> list[Finding]:
    """Every unsafe production setting. Empty means the config is acceptable.

    Pure and side-effect free so it can be unit-tested and also used by a
    pre-deploy config check without booting the app.
    """
    findings: list[Finding] = []
    findings += _check_secret_key(getattr(settings, "secret_key", None))
    findings += _check_demographics_key(
        getattr(settings, "demographics_encryption_key", None),
        required=bool(getattr(settings, "demographics_encryption_required", False)),
    )
    findings += _check_cors(
        getattr(settings, "cors_origins", None),
        allow_credentials=bool(getattr(settings, "cors_allow_credentials", True)),
    )
    findings += _check_database_url(getattr(settings, "database_url", None))
    findings += _check_debug(bool(getattr(settings, "debug", False)))
    return findings


def enforce(settings) -> None:
    """Refuse to start on unsafe production configuration.

    A warning would be ignored: the app works fine with a known signing key
    right up until someone forges a token, so this raises.
    """
    if not is_production(getattr(settings, "app_env", None)):
        logger.info(
            "Config validation skipped: APP_ENV=%s is not a production environment.",
            getattr(settings, "app_env", "unset"),
        )
        return

    findings = collect_findings(settings)
    if not findings:
        logger.info("Production configuration validated: no unsafe settings detected.")
        return

    # Setting names and problems only — never values.
    detail = "; ".join(str(f) for f in findings)
    raise ConfigurationError(
        f"Refusing to start with unsafe production configuration: {detail}. "
        "Set these via deployment secrets/environment variables."
    )
