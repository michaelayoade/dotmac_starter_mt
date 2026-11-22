"""Agent configuration: a root-owned TOML file, validated once, immutable afterwards."""

from __future__ import annotations

import base64
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

DB_ENV_EXACT: Final = frozenset(
    {
        "DATABASE_URL",
        "PLATFORM_DATABASE_URL",
        "DISPATCH_CONSUMPTION_DATABASE_URL",
        "LANE3_LEASE_DATABASE_URL",
    }
)
_ID: Final = re.compile(r"[a-z0-9][a-z0-9_.-]{0,62}")
_TARGET: Final = re.compile(
    r"([a-z0-9][a-z0-9_-]{0,62})/([a-z0-9][a-z0-9_-]{0,62})/([a-z0-9][a-z0-9_-]{0,62})"
)
_HEX64: Final = re.compile(r"[0-9a-f]{64}")


def refuse_db_environment(environ: Mapping[str, str]) -> None:
    """Inverted tripwire (DESIGN-rev3 s2.6): no CP DB credential, lease DSN or PG*."""
    for name in environ:
        if name in DB_ENV_EXACT or name.startswith("PG"):
            raise Refusal("db_environment_present")


def protected_config_file(path: Path) -> None:
    """Regular, not a symlink, root- or agent-owned, not group/other writable."""
    try:
        info = path.lstat()
    except OSError:
        raise Refusal("config_invalid") from None
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid not in (0, os.geteuid())
        or info.st_mode & 0o022
    ):
        raise Refusal("config_invalid")


@dataclass(frozen=True)
class AgentConfig:
    cp_url: str
    target_ref: str
    credential_id: str
    cp_response_public_keys_b64: tuple[str, ...]
    cp_spki_sha256: tuple[str, ...]
    state_dir: Path
    deploy_dir: Path
    foundation_argv: tuple[str, ...]
    openbao_url: str
    openbao_agent_config_dir: Path
    registry_binary: Path
    registry_binary_sha256: str
    skopeo_path: Path
    skopeo_sha256: str
    reconcile_containers: tuple[str, ...]
    cp_ca_file: Path | None = None
    runtime_root: Path = Path("/run/dotmac")
    pop_key_credential: str = "pop-key"  # nosec B105 - a credential NAME, not a value
    delivery_token_credential: str = "openbao-delivery-token"  # nosec B105
    legacy_source_path: Path | None = None
    docker_path: Path = Path("/usr/bin/docker")
    poll_seconds: int = 45
    foundation_timeout_seconds: int = 2700
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
        """`/run/dotmac/<product>`: the systemd RuntimeDirectory."""
        return self.runtime_root / self.product

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
        """`/run/dotmac/<product>/deploy`: params.env and the legacy copy live here."""
        return self.run_dir / "deploy"

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
        if (
            not self.cp_response_public_keys_b64
            or len(self.cp_response_public_keys_b64) > 2
        ):
            raise Refusal("config_invalid")
        for key in self.cp_response_public_keys_b64:
            try:
                raw = base64.b64decode(key, validate=True)
            except ValueError:
                raise Refusal("config_invalid") from None
            if len(raw) != 32:
                raise Refusal("config_invalid")
        # The SPKI pin is mandatory: the CP is pinned at enrolment (DESIGN-rev3 s3.1).
        if not self.cp_spki_sha256 or not all(
            _HEX64.fullmatch(pin) for pin in self.cp_spki_sha256
        ):
            raise Refusal("config_invalid")
        paths = [
            self.state_dir,
            self.deploy_dir,
            self.openbao_agent_config_dir,
            self.runtime_root,
            self.registry_binary,
            self.skopeo_path,
            self.docker_path,
        ]
        if self.cp_ca_file is not None:
            paths.append(self.cp_ca_file)
        if self.legacy_source_path is not None:
            paths.append(self.legacy_source_path)
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
        if not 30 <= self.poll_seconds <= 60:
            raise Refusal("config_invalid")
        if not 1 <= self.foundation_timeout_seconds <= 7200:
            raise Refusal("config_invalid")

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
        return cls.from_mapping(values)

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> AgentConfig:
        known = {
            "cp_url", "target_ref", "credential_id", "cp_response_public_keys_b64",
            "cp_spki_sha256", "state_dir", "deploy_dir", "foundation_argv",
            "openbao_url", "openbao_agent_config_dir", "cp_ca_file", "runtime_root",
            "pop_key_credential", "delivery_token_credential", "legacy_source_path",
            "poll_seconds", "foundation_timeout_seconds", "registry_binary",
            "registry_binary_sha256", "skopeo_path", "skopeo_sha256",
            "reconcile_containers", "docker_path",
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

        extra: dict[str, object] = {}
        for name in ("pop_key_credential", "delivery_token_credential"):
            if name in values:
                extra[name] = text(name)
        for name in ("poll_seconds", "foundation_timeout_seconds"):
            if name in values:
                number = values[name]
                if type(number) is not int:
                    raise Refusal("config_invalid")
                extra[name] = number
        if "docker_path" in values:
            extra["docker_path"] = Path(text("docker_path"))
        if "runtime_root" in values:
            extra["runtime_root"] = Path(text("runtime_root"))
        return cls(
            cp_url=text("cp_url"),
            target_ref=text("target_ref"),
            credential_id=text("credential_id"),
            cp_response_public_keys_b64=texts("cp_response_public_keys_b64"),
            cp_spki_sha256=texts("cp_spki_sha256"),
            state_dir=Path(text("state_dir")),
            deploy_dir=Path(text("deploy_dir")),
            foundation_argv=texts("foundation_argv"),
            openbao_url=text("openbao_url"),
            openbao_agent_config_dir=Path(text("openbao_agent_config_dir")),
            cp_ca_file=optional_path("cp_ca_file"),
            legacy_source_path=optional_path("legacy_source_path"),
            registry_binary=Path(text("registry_binary")),
            registry_binary_sha256=text("registry_binary_sha256"),
            skopeo_path=Path(text("skopeo_path")),
            skopeo_sha256=text("skopeo_sha256"),
            reconcile_containers=texts("reconcile_containers"),
            **extra,  # type: ignore[arg-type]
        )
