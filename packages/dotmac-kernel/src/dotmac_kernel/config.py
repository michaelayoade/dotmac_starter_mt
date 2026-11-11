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
    # A deployment fact, per ADR-0013: the module declares the question, the
    # deployment declares the answer. ADR-0003 Stage A ("dedicated one-tenant
    # deployment per ISP") is the safe default topology, and nothing enforced it.
    #
    # "single" or "multi". The deployment declares the MODE only — never WHICH
    # tenant. The tenant already exists as a row; naming it here too would be a
    # second source of truth that can drift or be mistyped, and a typo would
    # take the deployment down for no reason. Under "single" the kernel asserts
    # at startup that exactly one tenant row exists and binds to it.
    #
    # Defaults to "multi", which is the historical behaviour. Safe-by-default
    # would mean "single", but flipping it would change every existing
    # deployment at once; declare it explicitly everywhere first.
    tenancy: str = "multi"
    trusted_hosts: str = ""
    jwt_secret: str = "dev-insecure-change-me"
    session_hash_secret: str = "dev-insecure-change-me"
    jwt_ttl_seconds: int = 3600
    csrf_enabled: bool = True
    csrf_secret: str = "dev-insecure-change-me"
    csrf_token_ttl_seconds: int = 7200
    # Emit an outbox event when a setting changes, so a process holding derived
    # state learns of it. OFF by default: an event with no relay running is a
    # row that accumulates forever, and a deployment that runs no relay is
    # better off with no events than with a growing table. Turn it on where the
    # relay runs.
    settings_change_events: bool = False
    rate_limit_enabled: bool = True
    rate_limit_requests: int = 120
    rate_limit_window_seconds: int = 60
    # LRU cap on the in-memory rate-limit store (Task 5 — bounds worst-case
    # memory regardless of client behavior).
    rate_limit_max_keys: int = 10_000
    # RESERVED swap seam (contracts-not-implementations): a Redis-backed
    # RateLimitStore for multi-process deployments. No redis dependency
    # ships with the starter; setting this has no effect until a project
    # provides the store implementation. See dotmac_kernel/middleware/rate_limit.py.
    rate_limit_redis_url: str = ""
    # Stricter per-route limit for routes that accept ANONYMOUS callers (login,
    # token exchange, public forms). Opt-in by declaration: the kernel cannot
    # know which of a product's routes are anonymous, and guessing would either
    # throttle authenticated traffic or miss the route that matters. A
    # comma-separated list of route TEMPLATES as the router declares them
    # (`/auth/login`, `/forms/{token}`), each optionally prefixed by a method
    # (`POST /auth/login`). Empty (the default) disables the limiter and needs no
    # store. Keyed on the trusted-proxy client address when the assembly declares
    # `request_evidence`, otherwise on the transport peer -- never on a forwarded
    # header from an untrusted peer. See dotmac_kernel/middleware/rate_limit.py.
    anonymous_rate_limit_routes: str = ""
    anonymous_rate_limit_requests: int = 10
    anonymous_rate_limit_window_seconds: int = 60
    # `postgres` (the kernel `rate_limit_buckets` table, migration 0031; shared
    # across workers and nodes) or `memory` (process-local; development only).
    # PRODUCTION REFUSES `memory` when routes are declared: a per-process counter
    # multiplies the limit by the worker count and resets on every deploy.
    anonymous_rate_limit_store: str = "postgres"
    # HMAC key for the stored rate-limit key (the Postgres store keeps only an
    # HMAC-SHA256 of `tenant:client:route`, never the address or an unkeyed hash
    # an attacker could test offline). Resolved by NAME through Settings like the
    # other secrets; no default is accepted in production (see
    # `anonymous_rate_limit_errors`). Rotating it resets every open window.
    anonymous_rate_limit_key_secret: str = "dev-insecure-change-me"
    # Security response headers (Task 5). Disable only when a fronting
    # proxy owns these headers. Empty CSP = the computed strict default in
    # dotmac_kernel/middleware/security_headers.py. A non-empty compatibility
    # value may only tighten that baseline; weakening or partial policies fail
    # application construction.
    security_headers_enabled: bool = True
    content_security_policy: str = ""
    trust_inbound_request_id: bool = False
    disabled_features: str = ""  # comma-separated feature names
    # WHICH APPLICATION this deployment is, for attribution on commands and
    # audit rows (`dotmac_kernel.source_applications`). Empty means "derive it
    # from the assembly spec's own name", which is right for every assembly
    # whose name is already a well-formed code — the reference one is
    # `dotmac_starter_mt`. There is deliberately NO literal fallback such as
    # "app" or "system": a default identity that every deployment shares is an
    # anonymous principal with a name on it, and the whole point of the column
    # is to stop those existing. When neither the env nor the spec name yields
    # a usable code, no host identity is installed and anything this process
    # originates fails loudly at the audit write rather than recording a guess.
    source_application: str = ""
    # Comma-separated peer applications this deployment accepts attribution
    # from — the OTHER apps whose machine credentials and commands it will
    # honour. The host's own code is always accepted and does not need listing.
    # Empty is a real answer: "no peer application may call this deployment".
    accepted_source_applications: str = ""
    seed_on_startup: bool = True  # seed platform setting defaults in lifespan
    # Surface switch (F1): False mounts NO feature's web_routers (zero
    # /admin routes, no /static mount) — pure JSON API. Independent of
    # DISABLED_FEATURES, which turns off one named feature entirely, JSON
    # and web together. See dotmac_kernel.features's module docstring.
    web_enabled: bool = True

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"prod", "production"}

    @property
    def disabled_feature_set(self) -> set[str]:
        return {f.strip() for f in self.disabled_features.split(",") if f.strip()}

    @property
    def accepted_source_application_set(self) -> set[str]:
        return {
            code.strip()
            for code in self.accepted_source_applications.split(",")
            if code.strip()
        }


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
    if s.tenancy not in {"single", "multi"}:
        errors.append(f"TENANCY must be 'single' or 'multi', not {s.tenancy!r}")
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
    csrf_is_dev_default = s.csrf_secret == "dev-insecure-change-me"  # noqa: S105 # nosec B105
    if s.is_production and not s.csrf_enabled:
        errors.append("CSRF_ENABLED cannot be false in production")
    if s.is_production and csrf_is_dev_default:
        errors.append("CSRF_SECRET must be set in production")
    if s.is_production and len(s.csrf_secret.encode("utf-8")) < 32:
        errors.append("CSRF_SECRET must contain at least 32 bytes in production")
    if s.is_production and s.csrf_secret in {s.jwt_secret, s.session_hash_secret}:
        errors.append(
            "CSRF_SECRET must be distinct from JWT_SECRET and "
            "SESSION_HASH_SECRET in production"
        )
    if s.csrf_token_ttl_seconds < 1:
        errors.append("CSRF_TOKEN_TTL_SECONDS must be positive")
    errors.extend(anonymous_rate_limit_errors(s))
    return errors


ANONYMOUS_RATE_LIMIT_STORES: tuple[str, ...] = ("postgres", "memory")


def anonymous_rate_limit_errors(s: Settings) -> list[str]:
    """Configuration errors of the anonymous-route rate limit; empty when OK.

    Silent when no route is declared: an undeclared limiter needs no store, so a
    deployment that has not opted in is never refused on its account. Once routes
    are declared, production refuses the process-local store -- the limit it
    would enforce is per worker, not per client -- and says so by name.
    """
    if not any(r.strip() for r in s.anonymous_rate_limit_routes.split(",")):
        return []
    errors: list[str] = []
    if s.anonymous_rate_limit_requests < 1:
        errors.append("ANONYMOUS_RATE_LIMIT_REQUESTS must be positive")
    if s.anonymous_rate_limit_window_seconds < 1:
        errors.append("ANONYMOUS_RATE_LIMIT_WINDOW_SECONDS must be positive")
    store = s.anonymous_rate_limit_store.strip().lower()
    if store not in ANONYMOUS_RATE_LIMIT_STORES:
        errors.append(
            "ANONYMOUS_RATE_LIMIT_STORE must be one of "
            f"{list(ANONYMOUS_RATE_LIMIT_STORES)}, not {s.anonymous_rate_limit_store!r}"
        )
    elif s.is_production and store == "memory":
        errors.append(
            "ANONYMOUS_RATE_LIMIT_STORE=memory is refused in production: an "
            "in-memory counter is per worker process, so the anonymous-route "
            "limit would be multiplied by the worker count and reset on every "
            "restart. Use ANONYMOUS_RATE_LIMIT_STORE=postgres (kernel migration "
            "0031_rate_limit_buckets must be applied) or leave "
            "ANONYMOUS_RATE_LIMIT_ROUTES empty"
        )
    if s.is_production and store == "postgres":
        secret = s.anonymous_rate_limit_key_secret
        if secret == "dev-insecure-change-me":  # noqa: S105 # nosec B105
            errors.append(
                "ANONYMOUS_RATE_LIMIT_KEY_SECRET must be set in production when "
                "anonymous rate-limit routes are declared"
            )
        elif len(secret.encode("utf-8")) < 32:
            errors.append(
                "ANONYMOUS_RATE_LIMIT_KEY_SECRET must contain at least 32 bytes "
                "in production"
            )
        elif secret in {s.jwt_secret, s.session_hash_secret, s.csrf_secret}:
            errors.append(
                "ANONYMOUS_RATE_LIMIT_KEY_SECRET must be distinct from "
                "JWT_SECRET, SESSION_HASH_SECRET and CSRF_SECRET in production"
            )
    return errors


__all__ = [
    "ANONYMOUS_RATE_LIMIT_STORES",
    "Settings",
    "anonymous_rate_limit_errors",
    "settings",
    "validate_settings",
]
