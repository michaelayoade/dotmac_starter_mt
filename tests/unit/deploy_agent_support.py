"""Test doubles for dotmac-deploy-agent: a CP target-API double, OpenBao, a runner.

The CP double is also the executable statement of the wire contract the agent
codes against (DESIGN-rev3 s2.6): it VERIFIES the PoP signature, the credential
and target binding, the clock window and the nonce, and it SIGNS every response.
Documentation ranges and placeholders only.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from dotmac_deploy_agent import wire
from dotmac_deploy_agent.client import TargetClient
from dotmac_deploy_agent.commands import CommandResult, ReadOnlyCommands
from dotmac_deploy_agent.config import AgentConfig
from dotmac_deploy_agent.launcher import Launcher
from dotmac_deploy_agent.lease import LeaseClient
from dotmac_deploy_agent.openbao import OpenBaoClient
from dotmac_deploy_agent.params import Param, canonical_render, render_digest
from dotmac_deploy_agent.refusal import Refusal
from dotmac_deploy_agent.runner import RunResult, RunSpec
from dotmac_deploy_agent.transfer import TransferResult
from dotmac_deploy_agent.transport import Response

TARGET = "erp/staging/host-a"
NOW = datetime(2027, 1, 6, 12, 0, 0, tzinfo=UTC)
IMAGE_DIGEST = "sha256:" + "a" * 64
APP_IMAGE = f"127.0.0.1:5000/org/app@{IMAGE_DIGEST}"
MANIFEST_DIGEST = IMAGE_DIGEST
PREVIOUS_IMAGE = "127.0.0.1:5000/org/app@sha256:" + "b" * 64


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def public_b64(key: Ed25519PrivateKey) -> str:
    return b64(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))


@dataclass
class Keys:
    agent: Ed25519PrivateKey = field(default_factory=Ed25519PrivateKey.generate)
    cp: Ed25519PrivateKey = field(default_factory=Ed25519PrivateKey.generate)

    def seed(self) -> bytes:
        from cryptography.hazmat.primitives.serialization import (
            NoEncryption,
            PrivateFormat,
        )

        return b64(
            self.agent.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
        ).encode()


def make_config(tmp_path: Path, keys: Keys, **overrides: object) -> AgentConfig:
    root = tmp_path / "run"
    root.mkdir(exist_ok=True)
    state = tmp_path / "state"
    state.mkdir(exist_ok=True, mode=0o700)
    app = tmp_path / "app"
    app.mkdir(exist_ok=True)
    values: dict[str, object] = {
        "cp_url": "https://cp.example.invalid",
        "target_ref": TARGET,
        "credential_id": "cred-1",
        "cp_response_public_keys_b64": (public_b64(keys.cp),),
        "cp_spki_sha256": ("c" * 64,),
        "state_dir": state,
        "deploy_dir": app,
        "foundation_argv": ("/usr/local/bin/foundation-run",),
        "openbao_url": "http://192.0.2.10:8200",
        "openbao_agent_config_dir": state / "bao",
        "registry_binary": Path("/usr/local/bin/registry"),
        "registry_binary_sha256": "d" * 64,
        "skopeo_path": Path("/usr/bin/skopeo"),
        "skopeo_sha256": "e" * 64,
        "reconcile_containers": ("erp-app",),
        "runtime_root": root,
    }
    values.update(overrides)
    return AgentConfig(**values)  # type: ignore[arg-type]


def base_params() -> list[dict[str, object]]:
    return [
        {"name": "app_port", "material": "APP_PORT", "type": "int", "value": "8080"},
        {
            "name": "secrets_dir_app",
            "material": "SECRETS_DIR_APP",
            "type": "path",
            "value": "/run/dotmac/erp/app/secrets",
        },
        {"name": "note", "material": None, "type": "string", "value": "ignored"},
    ]


def to_params(rows: list[dict[str, object]]) -> list[Param]:
    return [
        Param(str(r["name"]), r["material"], str(r["type"]), str(r["value"]))  # type: ignore[arg-type]
        for r in rows
    ]


def base_images() -> list[dict[str, object]]:
    return [
        {
            "name": "app",
            "source": "transfer",
            "repository": "registry.example.invalid/org/app",
            "manifest_digest": MANIFEST_DIGEST,
            "planned_repository": "127.0.0.1:5000/org/app",
            "image_id": "sha256:" + "f" * 64,
            "archive": "app.oci.tar",
        }
    ]


def make_materials(**overrides: object) -> dict[str, object]:
    params = overrides.pop("params", None) or base_params()
    app_image = str(overrides.pop("app_image", APP_IMAGE))
    document: dict[str, object] = {
        "schema": "dotmac.deploy_target_materials/1",
        "pending": True,
        "dispatch_id": "dispatch-1",
        "target_ref": TARGET,
        "params": params,
        "app_image": app_image,
        "previous_app_image": PREVIOUS_IMAGE,
        "params_env_digest": render_digest(
            canonical_render(to_params(params), app_image)  # type: ignore[arg-type]
        ),
        "bindings": [],
        "legacy": None,
        "images": base_images(),
        "lease_ttl_seconds": 900,
    }
    document.update(overrides)
    return document


class FakeCp:
    """The CP target API as the agent sees it. Verifies requests, signs responses."""

    def __init__(self, keys: Keys, clock: Callable[[], datetime] = lambda: NOW) -> None:
        self.keys = keys
        self.clock = clock
        self.materials: dict[str, object] | None = None
        self.observations: list[dict[str, object]] = []
        self.nonces: set[str] = set()
        self.calls: list[tuple[str, str]] = []
        self.overrides: dict[str, tuple[int, dict[str, object]]] = {}
        self.store_now_override: str | None = None
        self.tamper_signature = False
        self.omit_signature = False
        self.status_5xx = False
        self.handlers: dict[str, Callable[[dict[str, object]], dict[str, object]]] = {}
        self.standing = "CLEAN"
        self.lease_log: list[tuple[str, dict[str, object]]] = []
        self.handlers = {
            "lease/standing": lambda _p: {"standing": self.standing},
            "lease/acquire": self._acquire,
            "lease/covers": lambda p: self._log("lease/covers", p) | {"covers": True},
            "lease/release": lambda p: self._log("lease/release", p)
            | {"released": True},
        }

    def _log(self, endpoint: str, payload: dict[str, object]) -> dict[str, object]:
        self.lease_log.append((endpoint, payload))
        return {}

    def _acquire(self, payload: dict[str, object]) -> dict[str, object]:
        self._log("lease/acquire", payload)
        return {"lease_id": "lease-1", "ttl_seconds": payload["ttl_seconds"]}

    def request(
        self, method: str, path: str, headers: Mapping[str, str], body: bytes
    ) -> Response:
        nonce = headers[wire.H_NONCE]
        message = wire.request_message(
            method=method,
            path=path,
            body=body,
            credential_id=headers[wire.H_CREDENTIAL],
            target_ref=headers[wire.H_TARGET],
            issued_at=headers[wire.H_ISSUED_AT],
            nonce=nonce,
        )
        public = Ed25519PublicKey.from_public_bytes(
            self.keys.agent.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        )
        try:
            public.verify(base64.b64decode(headers[wire.H_SIGNATURE]), message)
        except InvalidSignature:
            return self._reply(method, path, nonce, 401, {"error": "bad_signature"})
        prefix = f"{wire.BASE_PATH}/targets/"
        target, _, endpoint = path[len(prefix) :].partition("/")
        if unquote(target) != headers[wire.H_TARGET] or nonce in self.nonces:
            return self._reply(method, path, nonce, 403, {"error": "target_or_replay"})
        self.nonces.add(nonce)
        self.calls.append((method, endpoint))
        if self.status_5xx:
            return Response(503, {}, b"upstream")
        if endpoint in self.overrides:
            status, doc = self.overrides[endpoint]
            return self._reply(method, path, nonce, status, doc)
        payload = json.loads(body) if body else {}
        if endpoint == "materials":
            doc = self.materials or {"pending": False}
            return self._reply(method, path, nonce, 200, dict(doc))
        if endpoint == "observations":
            self.observations.append(payload)
            return self._reply(method, path, nonce, 200, {"accepted": True})
        if endpoint in self.handlers:
            return self._reply(
                method, path, nonce, 200, self.handlers[endpoint](payload)
            )
        return self._reply(method, path, nonce, 404, {"error": "no_such_endpoint"})

    def _reply(
        self, method: str, path: str, nonce: str, status: int, doc: dict[str, object]
    ) -> Response:
        doc = dict(doc)
        doc["store_now"] = self.store_now_override or wire.utc_stamp(self.clock())
        body = json.dumps(doc).encode()
        signature = self.keys.cp.sign(
            wire.response_message(
                method=method, path=path, request_nonce=nonce, status=status, body=body
            )
        )
        headers = {wire.H_RESPONSE_SIGNATURE.lower(): b64(signature)}
        if self.tamper_signature:
            headers = {wire.H_RESPONSE_SIGNATURE.lower(): b64(b"x" * 64)}
        if self.omit_signature:
            headers = {}
        return Response(status, headers, body)


class FakeBaoTransport:
    """OpenBao responses for the delivery identity. Records paths, never values."""

    SECRET = "secret-id-value-for-tests"

    def __init__(self) -> None:
        self.paths: list[str] = []

    def request(
        self, method: str, path: str, headers: Mapping[str, str], body: bytes
    ) -> Response:
        self.paths.append(path)
        if path.endswith("/role-id"):
            return Response(
                200, {}, json.dumps({"data": {"role_id": "role-id-1"}}).encode()
            )
        if path.endswith("/secret-id"):
            assert headers.get("X-Vault-Wrap-TTL")
            return Response(
                200, {}, json.dumps({"wrap_info": {"token": "wrap-1"}}).encode()
            )
        if path == "/v1/sys/wrapping/unwrap":
            assert headers["X-Vault-Token"] == "wrap-1"
            return Response(
                200, {}, json.dumps({"data": {"secret_id": self.SECRET}}).encode()
            )
        return Response(404, {}, b"{}")


class FakeRunner:
    def __init__(
        self,
        exit_code: int = 0,
        timed_out: bool = False,
        effect: Callable[[RunSpec], None] | None = None,
    ) -> None:
        self.specs: list[RunSpec] = []
        self.exit_code = exit_code
        self.timed_out = timed_out
        self.effect = effect
        self.seen_params: bytes | None = None
        self.seen_mode: int | None = None

    def run(self, spec: RunSpec) -> RunResult:
        self.specs.append(spec)
        self.seen_params = spec.params_env.read_bytes()
        self.seen_mode = spec.params_env.stat().st_mode & 0o777
        if self.effect is not None:
            self.effect(spec)
        return RunResult(None if self.timed_out else self.exit_code, self.timed_out)


class FakeTransfer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.error: Refusal | None = None

    def transfer_all(self, images, *, ttl_seconds):  # type: ignore[no-untyped-def]
        self.calls.append(tuple(i.name for i in images))
        if self.error is not None:
            raise self.error
        return tuple(
            TransferResult(
                i.name,
                i.image_id or "sha256:" + "0" * 64,
                i.planned_reference,
                True,
                True,
            )
            for i in images
            if i.source == "transfer"
        )


class FakeCommands:
    """Scripted external commands. `script` maps a verb tuple to a result."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.script: dict[tuple[str, ...], CommandResult] = {}

    def run(self, argv: tuple[str, ...], *, timeout: float) -> CommandResult:
        self.calls.append(argv)
        for verbs, result in self.script.items():
            if argv[1 : 1 + len(verbs)] == verbs:
                return result
        return CommandResult(0, b"")


@dataclass
class Rig:
    config: AgentConfig
    keys: Keys
    cp: FakeCp
    runner: FakeRunner
    bao: FakeBaoTransport
    launcher: Launcher
    environ: dict[str, str]
    transfer: FakeTransfer
    commands: FakeCommands


def make_rig(
    tmp_path: Path,
    *,
    runner: FakeRunner | None = None,
    environ: dict[str, str] | None = None,
    **config_overrides: object,
) -> Rig:
    keys = Keys()
    config = make_config(tmp_path, keys, **config_overrides)
    cp = FakeCp(keys)
    bao = FakeBaoTransport()
    runner = runner or FakeRunner()
    env = environ if environ is not None else {}
    signer = wire.PopSigner.from_seed(config.credential_id, keys.seed())
    client = TargetClient(config, signer, cp, clock=lambda: NOW)
    transfer = FakeTransfer()
    commands = FakeCommands()
    launcher = Launcher(
        config,
        client,
        runner,
        OpenBaoClient(bao, "delivery-token", "staging/erp"),
        LeaseClient(client),
        transfer,
        ReadOnlyCommands(commands),
        environ=env,
        clock=lambda: NOW,
    )
    return Rig(config, keys, cp, runner, bao, launcher, env, transfer, commands)
