"""Agent configuration: a root-owned TOML file, validated once, immutable afterwards."""

from __future__ import annotations

import os
import re
import stat
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from urllib.parse import urlparse

from .refusal import Refusal
from .wire import decode_b64url, is_denied_public_key

DB_ENV_EXACT: Final = frozenset(
    {
        "DATABASE_URL",
        "PLATFORM_DATABASE_URL",
        "DISPATCH_CONSUMPTION_DATABASE_URL",
        "LANE3_LEASE_DATABASE_URL",
    }
)
_ID: Final = re.compile(r"[a-z0-9][a-z0-9_.-]{0,62}")
# Control's target-ref grammar: three lowercase segments (target_api_v1 `paths`).
_SEG = r"([a-z0-9][a-z0-9._-]{0,63})"
_SEG_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_TARGET: Final = re.compile(f"{_SEG}/{_SEG}/{_SEG}")
_HEX64: Final = re.compile(r"[0-9a-f]{64}")


def refuse_db_environment(environ: Mapping[str, str]) -> None:
    """Inverted tripwire (DESIGN-rev3 s2.6): no CP DB credential, lease DSN or PG*."""
    for name in environ:
        if name in DB_ENV_EXACT or name.startswith("PG"):
            raise Refusal("db_environment_present")


def protected_config_file(path: Path, *, owner_uid: int = 0) -> None:
    """Regular, not a symlink, ROOT-owned, not group/other writable: the agent
    cannot rewrite its own keys or argv for persistence."""
    try:
        info = path.lstat()
    except OSError:
        raise Refusal("config_invalid") from None
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != owner_uid
        or info.st_mode & 0o022
    ):
        raise Refusal("config_invalid")


BINDINGS_ENV_REQUIRED: Final = (
    "AUTHORIZATION_VERIFICATION_IDENTITY",
    "DISPATCH_VERIFICATION_IDENTITY",
    "EXECUTION_CONTEXT_MATERIALS",
    "HOST_ADMISSION_MATERIALS",
    "RELEASE_EVIDENCE_REPOSITORY",
    "RELEASE_EVIDENCE_VERIFICATION_IDENTITY",
)
BINDINGS_ENV_OPTIONAL: Final = ("DISPATCH_CLOCK_SKEW_LIMIT_SECONDS",)
_PATH_VALUED: Final = re.compile(r".*_(IDENTITY|MATERIALS)$")


def _check_bindings_env(pairs: tuple[tuple[str, str], ...]) -> None:
    """The names the CP bindings already require, taken from the root-owned config."""
    names = [name for name, _ in pairs]
    allowed = set(BINDINGS_ENV_REQUIRED) | set(BINDINGS_ENV_OPTIONAL)
    if len(set(names)) != len(names) or not set(names) <= allowed:
        raise Refusal("config_invalid")
    if not set(BINDINGS_ENV_REQUIRED) <= set(names):
        raise Refusal("config_invalid")
    for name, value in pairs:
        if (
            not value
            or len(value) > 4096
            or "\n" in value
            or "\x00" in value
            or value != value.strip()
            or (_PATH_VALUED.fullmatch(name) and not os.path.isabs(value))
        ):
            raise Refusal("config_invalid")


@dataclass(frozen=True)
class AgentConfig:
    cp_url: str
    target_ref: str
    credential_id: str
    cp_response_public_keys_b64url: tuple[str, ...]
    cp_spki_sha256: tuple[str, ...]
    state_dir: Path
    deploy_dir: Path
    foundation_argv: tuple[str, ...]
    openbao_url: str
    openbao_config_dir: Path
    registry_binary: Path
    registry_binary_sha256: str
    skopeo_path: Path
    skopeo_sha256: str
    skopeo_policy: Path
    reconcile_containers: tuple[str, ...]
    bindings_env: tuple[tuple[str, str], ...]
    #: Enrolment-pinned OpenBao naming (the same values the kit templates use).
    #: The signed plan stays the source; a plan that differs is refused.
    secrets_namespace: str
    secrets_environment: str
    cp_ca_file: Path | None = None
    #: The agent's OWN runtime state (params.env, inbox, registry): its own
    #: RuntimeDirectory, so stopping the agent never deletes the per-role
    #: directories that tmpfiles.d owns under `secrets_root`.
    runtime_root: Path = Path("/run/dotmac-deploy-agent")
    secrets_root: Path = Path("/run/dotmac")
    pop_key_credential: str = "pop-key"  # nosec B105 - a credential NAME, not a value
    delivery_role_id_credential: str = "delivery-role-id"
    delivery_secret_id_credential: str = "delivery-secret-id"  # nosec B105
    legacy_source_path: Path | None = None
    docker_path: Path = Path("/usr/bin/docker")
    systemctl_path: Path = Path("/usr/bin/systemctl")
    runtime_secret_id_ttl_seconds: int = 35 * 86400
    migrate_secret_id_ttl_seconds: int = 900
    wrap_ttl_seconds: int = 120
    dispatch_max_age_seconds: int = 6 * 3600
    settle_seconds: int = 600
    #: Host fence for role jobs (root-owned config, enrolment op): a signed
    #: `dispatched_job.role` that is not listed is refused. Empty by default, so
    #: every role job is refused until enrolment names one.
    job_roles: tuple[str, ...] = ()
    #: Host root of job inputs and outputs (`<root>/<job>/in|out`); root-owned config.
    job_work_root: Path | None = None
    #: The target-API generation the agent speaks. 1 (default) posts today's
    #: unsigned `dotmac.deploy_observation/1`. 2 is the PoP-signed observation
    #: envelope of Control's `target_api_v2.json` (vendored beside v1). The default
    #: moves to 2 once every v2 vector passes on hosted CI.
    target_api_version: int = 1
    poll_seconds: int = 45
    foundation_timeout_seconds: int = 2700
    config_path: Path | None = None
    version: str = "0.1.0a1"

    @property
    def product(self) -> str:
        return self.target_parts[0]

    @property
    def environment(self) -> str:
        return self.target_parts[1]

    @property
    def target_parts(self) -> tuple[str, str, str]:
        match = _TARGET.fullmatch(self.target_ref)
        if match is None:
            raise Refusal("config_invalid")
        return (match[1], match[2], match[3])

    @property
    def run_dir(self) -> Path:
        """The agent's private runtime directory (0700), one per instance."""
        return self.runtime_root / self.product

    def role_dir(self, role: str) -> Path:
        """`/run/dotmac/<secrets_namespace>/<role>`: tmpfiles.d-owned, and the ONE
        naming key for every per-role artefact (unit, HCL, drop-in, this)."""
        return self.secrets_root / self.secrets_namespace / role

    @property
    def inbox_dir(self) -> Path:
        """`/run/dotmac/<product>/images`: the attended archive inbox (0700)."""
        return self.run_dir / "images"

    @property
    def registry_dir(self) -> Path:
        """Transient registry config and tmpfs storage; deleted at teardown."""
        return self.run_dir / "registry"

    @property
    def deploy_run_dir(self) -> Path:
        """`<runtime_root>/<product>/deploy` (the agent's own RuntimeDirectory, never
        `/run/dotmac`): params.env, the legacy copy and the capture file live here."""
        return self.run_dir / "deploy"

    @property
    def dispatch_identity_path(self) -> Path:
        """The enrolment-pinned dispatch identity (the same file the bindings get)."""
        return Path(dict(self.bindings_env)["DISPATCH_VERIFICATION_IDENTITY"])

    def __post_init__(self) -> None:
        _ = self.target_parts
        url = urlparse(self.cp_url)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path not in ("", "/")
        ):
            raise Refusal("config_invalid")
        # OpenBao is reached over WireGuard only, where plain HTTP is the ruled
        # transport (DESIGN-rev3 s3.2); it must still be a bare origin.
        bao = urlparse(self.openbao_url)
        if (
            bao.scheme not in ("http", "https")
            or not bao.hostname
            or bao.username
            or bao.password
            or bao.query
            or bao.fragment
            or bao.path not in ("", "/")
        ):
            raise Refusal("config_invalid")
        if not _ID.fullmatch(self.credential_id):
            raise Refusal("config_invalid")
        # The CP response key is a DISTINCT key from Control's dispatch key (U13).
        if (
            not self.cp_response_public_keys_b64url
            or len(self.cp_response_public_keys_b64url) > 2
            or any(
                decode_b64url(key, length=32) is None
                for key in self.cp_response_public_keys_b64url
            )
        ):
            raise Refusal("config_invalid")
        # Control's vector file publishes two private seeds: their public keys
        # authenticate nobody and may never be pinned as the CP response key.
        if any(is_denied_public_key(k) for k in self.cp_response_public_keys_b64url):
            raise Refusal("config_invalid", "denied_public_key")
        # The SPKI pin is mandatory: the CP is pinned at enrolment (DESIGN-rev3 s3.1).
        if not self.cp_spki_sha256 or not all(
            _HEX64.fullmatch(pin) for pin in self.cp_spki_sha256
        ):
            raise Refusal("config_invalid")
        paths = [
            self.state_dir,
            self.deploy_dir,
            self.openbao_config_dir,
            self.runtime_root,
            self.secrets_root,
            self.registry_binary,
            self.skopeo_path,
            self.skopeo_policy,
            self.docker_path,
            self.systemctl_path,
        ]
        if self.cp_ca_file is not None:
            paths.append(self.cp_ca_file)
        if self.legacy_source_path is not None:
            paths.append(self.legacy_source_path)
        if self.config_path is not None:
            paths.append(self.config_path)
        if not all(path.is_absolute() for path in paths):
            raise Refusal("config_invalid")
        if (
            not self.foundation_argv
            or not os.path.isabs(self.foundation_argv[0])
            or not all(isinstance(part, str) and part for part in self.foundation_argv)
        ):
            raise Refusal("config_invalid")
        # The registry binary and skopeo are pinned at enrolment (DESIGN-rev3 s2.7).
        if not _HEX64.fullmatch(self.registry_binary_sha256) or not _HEX64.fullmatch(
            self.skopeo_sha256
        ):
            raise Refusal("config_invalid")
        if not self.reconcile_containers or not all(
            _ID.fullmatch(name) for name in self.reconcile_containers
        ):
            raise Refusal("config_invalid")
        _check_bindings_env(self.bindings_env)
        for value in (self.secrets_namespace, self.secrets_environment):
            if not _SEG_RE.fullmatch(value):
                raise Refusal("config_invalid", "bad_secrets_name")
        if not 300 <= self.dispatch_max_age_seconds <= 7 * 86400:
            raise Refusal("config_invalid")
        if not 60 <= self.settle_seconds <= 86400:
            raise Refusal("config_invalid")
        if not all(_SEG_RE.fullmatch(r) for r in self.job_roles) or len(
            set(self.job_roles)
        ) != len(self.job_roles):
            raise Refusal("config_invalid")
        if self.job_work_root is not None and not self.job_work_root.is_absolute():
            raise Refusal("config_invalid")
        if self.target_api_version not in (1, 2):
            raise Refusal("config_invalid")
        if not 30 <= self.poll_seconds <= 60:
            raise Refusal("config_invalid")
        if not 1 <= self.foundation_timeout_seconds <= 7200:
            raise Refusal("config_invalid")
        if not 60 <= self.wrap_ttl_seconds <= 300:  # the policy's wrapping bounds
            raise Refusal("config_invalid")
        if not 60 <= self.migrate_secret_id_ttl_seconds <= 3600:
            raise Refusal("config_invalid")
        if not 3600 <= self.runtime_secret_id_ttl_seconds <= 90 * 86400:
            raise Refusal("config_invalid")

    def check_instance(self, environ: Mapping[str, str]) -> None:
        """The systemd instance name IS the product segment of `target_ref`: the unit's
        `RuntimeDirectory`/`StateDirectory` (`.../%i`) must be the directories this
        config names, or two instances could share or cross state. Outside systemd
        (neither variable set) there is nothing to compare."""
        runtime = environ.get("RUNTIME_DIRECTORY")
        state = environ.get("STATE_DIRECTORY")
        if runtime is not None and Path(runtime) != self.run_dir:
            raise Refusal("config_invalid", "instance_runtime_dir")
        if state is not None and Path(state) != self.state_dir:
            raise Refusal("config_invalid", "instance_state_dir")

    @classmethod
    def load(cls, path: Path) -> AgentConfig:
        if os.geteuid() == 0:
            raise Refusal("config_invalid")  # a dedicated, non-root agent user only
        protected_config_file(path)
        try:
            with path.open("rb") as stream:
                values = tomllib.load(stream)
        except (OSError, tomllib.TOMLDecodeError):
            raise Refusal("config_invalid") from None
        return cls.from_mapping(values, config_path=path)

    @classmethod
    def from_mapping(
        cls, values: Mapping[str, object], *, config_path: Path | None = None
    ) -> AgentConfig:
        known = {
            "cp_url", "target_ref", "credential_id", "cp_response_public_keys_b64url",
            "cp_spki_sha256", "state_dir", "deploy_dir", "foundation_argv",
            "openbao_url", "openbao_config_dir", "cp_ca_file", "runtime_root",
            "pop_key_credential", "delivery_role_id_credential",
            "delivery_secret_id_credential", "legacy_source_path", "poll_seconds",
            "foundation_timeout_seconds", "registry_binary", "registry_binary_sha256",
            "skopeo_path", "skopeo_sha256", "skopeo_policy", "reconcile_containers",
            "docker_path", "systemctl_path", "bindings_env",
            "runtime_secret_id_ttl_seconds", "migrate_secret_id_ttl_seconds",
            "wrap_ttl_seconds", "secrets_namespace", "secrets_environment",
            "secrets_root", "dispatch_max_age_seconds", "settle_seconds", "job_roles",
            "target_api_version", "job_work_root",
        }  # fmt: skip
        if not set(values) <= known:
            raise Refusal("config_invalid")

        def text(name: str) -> str:
            value = values.get(name)
            if not isinstance(value, str):
                raise Refusal("config_invalid")
            return value

        def texts(name: str) -> tuple[str, ...]:
            value = values.get(name)
            if not isinstance(value, list) or not all(
                isinstance(v, str) for v in value
            ):
                raise Refusal("config_invalid")
            return tuple(value)

        def optional_path(name: str) -> Path | None:
            return Path(text(name)) if name in values else None

        table = values.get("bindings_env")
        if not isinstance(table, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in table.items()
        ):
            raise Refusal("config_invalid")
        pairs = tuple(sorted((str(k), str(v)) for k, v in table.items()))

        extra: dict[str, object] = {}
        for name in (
            "pop_key_credential",
            "delivery_role_id_credential",
            "delivery_secret_id_credential",
        ):
            if name in values:
                extra[name] = text(name)
        for name in ("docker_path", "systemctl_path", "runtime_root", "secrets_root"):
            if name in values:
                extra[name] = Path(text(name))
        for name in (
            "poll_seconds",
            "foundation_timeout_seconds",
            "runtime_secret_id_ttl_seconds",
            "migrate_secret_id_ttl_seconds",
            "wrap_ttl_seconds",
            "dispatch_max_age_seconds",
            "settle_seconds",
        ):
            if name in values:
                number = values[name]
                if type(number) is not int:
                    raise Refusal("config_invalid")
                extra[name] = number
        if "target_api_version" in values:
            version = values["target_api_version"]
            if type(version) is not int:
                raise Refusal("config_invalid")
            extra["target_api_version"] = version
        if "job_work_root" in values:
            extra["job_work_root"] = Path(text("job_work_root"))
        if "job_roles" in values:
            extra["job_roles"] = texts("job_roles")
        return cls(
            cp_url=text("cp_url"),
            target_ref=text("target_ref"),
            credential_id=text("credential_id"),
            cp_response_public_keys_b64url=texts("cp_response_public_keys_b64url"),
            cp_spki_sha256=texts("cp_spki_sha256"),
            state_dir=Path(text("state_dir")),
            deploy_dir=Path(text("deploy_dir")),
            foundation_argv=texts("foundation_argv"),
            openbao_url=text("openbao_url"),
            openbao_config_dir=Path(text("openbao_config_dir")),
            cp_ca_file=optional_path("cp_ca_file"),
            legacy_source_path=optional_path("legacy_source_path"),
            registry_binary=Path(text("registry_binary")),
            registry_binary_sha256=text("registry_binary_sha256"),
            skopeo_path=Path(text("skopeo_path")),
            skopeo_sha256=text("skopeo_sha256"),
            skopeo_policy=Path(text("skopeo_policy")),
            reconcile_containers=texts("reconcile_containers"),
            bindings_env=pairs,
            secrets_namespace=text("secrets_namespace"),
            secrets_environment=text("secrets_environment"),
            config_path=config_path,
            **extra,  # type: ignore[arg-type]
        )
