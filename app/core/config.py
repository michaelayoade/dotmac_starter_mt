"""Application configuration.

Read from environment. Fail-closed in production for required values.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "dev"
    database_url: str = ""
    platform_database_url: str = ""
    migration_database_url: str = ""
    platform_root_domain: str = "localhost"
    trusted_hosts: str = ""
    jwt_secret: str = "dev-insecure-change-me"
    session_hash_secret: str = "dev-insecure-change-me"
    jwt_ttl_seconds: int = 3600
    csrf_enabled: bool = True
    rate_limit_enabled: bool = True
    rate_limit_requests: int = 120
    rate_limit_window_seconds: int = 60
    # LRU cap on the in-memory rate-limit store (Task 5 — bounds worst-case
    # memory regardless of client behavior).
    rate_limit_max_keys: int = 10_000
    # RESERVED swap seam (contracts-not-implementations): a Redis-backed
    # RateLimitStore for multi-process deployments. No redis dependency
    # ships with the starter; setting this has no effect until a project
    # provides the store implementation. See app/core/middleware/rate_limit.py.
    rate_limit_redis_url: str = ""
    # Security response headers (Task 5). Disable only when a fronting
    # proxy owns these headers. Empty CSP = the computed strict default in
    # app/core/middleware/security_headers.py.
    security_headers_enabled: bool = True
    content_security_policy: str = ""
    trust_inbound_request_id: bool = False
    disabled_features: str = ""  # comma-separated feature names
    seed_on_startup: bool = True  # seed platform setting defaults in lifespan
    # Surface switch (F1): False mounts NO feature's web_routers (zero
    # /admin routes, no /static mount) — pure JSON API. Independent of
    # DISABLED_FEATURES, which turns off one named feature entirely, JSON
    # and web together. See app.core.features's module docstring.
    web_enabled: bool = True

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"prod", "production"}

    @property
    def disabled_feature_set(self) -> set[str]:
        return {f.strip() for f in self.disabled_features.split(",") if f.strip()}


settings = Settings()


def validate_settings(s: Settings) -> list[str]:
    """Return list of fatal errors (empty if OK). Caller raises if non-empty in prod."""
    errors: list[str] = []
    if not s.database_url:
        errors.append("DATABASE_URL is required")
    if s.is_production and not s.platform_database_url:
        errors.append("PLATFORM_DATABASE_URL is required in production")
    if s.is_production and not s.trusted_hosts:
        errors.append("TRUSTED_HOSTS is required in production")
    if s.is_production and s.platform_root_domain in {"localhost", ""}:
        errors.append("PLATFORM_ROOT_DOMAIN must be a real domain in production")
    # Sentinel comparisons to fail closed in prod; not real secrets.
    jwt_is_dev_default = s.jwt_secret == "dev-insecure-change-me"  # noqa: S105 # nosec B105
    session_is_dev_default = (
        s.session_hash_secret == "dev-insecure-change-me"  # noqa: S105 # nosec B105
    )
    if s.is_production and jwt_is_dev_default:
        errors.append("JWT_SECRET must be set in production")
    if s.is_production and session_is_dev_default:
        errors.append("SESSION_HASH_SECRET must be set in production")
    return errors
