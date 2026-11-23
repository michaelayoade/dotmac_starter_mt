"""Test doubles for dotmac-deploy-agent: a CP target-API double, OpenBao, a runner.

The CP double is also the executable statement of the wire contract the agent
codes against (DESIGN-rev3 s2.6, ruling R1): it VERIFIES the PoP signature, the
credential and target binding, the clock window and the nonce, and it SIGNS every
response. Materials carry a Control-signed dispatch envelope and its plan
snapshot, built here with a stand-in Control dispatch key.
Documentation ranges and placeholders only.
"""
# ruff: noqa: E501

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)
from dotmac_deploy_agent import wire
from dotmac_deploy_agent.canon import canonical_json
from dotmac_deploy_agent.client import TargetClient
from dotmac_deploy_agent.commands import CommandResult, ReadOnlyCommands
from dotmac_deploy_agent.config import AgentConfig
from dotmac_deploy_agent.dispatch import fingerprint_of, plan_digest_of
from dotmac_deploy_agent.launcher import Launcher
from dotmac_deploy_agent.lease import LeaseClient
from dotmac_deploy_agent.params import Param, canonical_render, render_digest
from dotmac_deploy_agent.refusal import Refusal
from dotmac_deploy_agent.runner import RunResult, RunSpec
from dotmac_deploy_agent.transfer import TransferResult
from dotmac_deploy_agent.transport import Response
from dotmac_deploy_agent.wire import encode_b64url

from tests.unit.deploy_agent_server import (
    RequestRefused,
    check_request,
    request_credential_id,
)

TARGET = "erp/staging/host-a"
NOW = datetime(2027, 1, 6, 12, 0, 0, tzinfo=UTC)
IMAGE_DIGEST = "sha256:" + "a" * 64
APP_IMAGE = f"127.0.0.1:5000/org/app@{IMAGE_DIGEST}"
PREVIOUS_IMAGE = "127.0.0.1:5000/org/app@sha256:" + "b" * 64  # a previously applied pin
DISPATCH_KEY_ID = "control-dispatch-1"
#: A unit that entered `active` BEFORE the rig's epoch (1_800_000_000).
UNIT_ACTIVE = (
    b"ActiveState=active\nNRestarts=0\nActiveEnterTimestampMonotonic=1000\n"
    b"ActiveEnterTimestamp=@1799999000\n"
)
#: What an authored OpenBao Agent config must carry (live lines, both true).
AUTHORED = (
    "# authored config\n"
    'auto_auth {\n  method "approle" {\n    exit_on_err = true\n  }\n}\n'
    "template_config {\n  exit_on_retry_failure = true\n}\n"
)

#: A stand-in for Control's dispatch signing key. Shared so `make_materials()`
#: works without a rig; a test that needs a rogue signer passes its own.
DISPATCH_KEY = Ed25519PrivateKey.generate()


def public_b64url(key: Ed25519PrivateKey) -> str:
    return encode_b64url(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))


@dataclass
class Keys:
    agent: Ed25519PrivateKey = field(default_factory=Ed25519PrivateKey.generate)
    cp: Ed25519PrivateKey = field(default_factory=Ed25519PrivateKey.generate)

    def seed(self) -> bytes:
        return encode_b64url(
            self.agent.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
        ).encode()


def write_identity(path: Path, key: Ed25519PrivateKey = DISPATCH_KEY) -> None:
    """`PlatformCpPublicVerificationIdentity.v1` for the stand-in dispatch key."""
    raw = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema": "PlatformCpPublicVerificationIdentity.v1",
                "key_id": DISPATCH_KEY_ID,
                "algorithm": "ed25519",
                "purpose": "deployment_dispatch",
                "public_key_b64url": encode_b64url(raw),
                "public_key_fingerprint": fingerprint_of(raw),
            }
        )
    )
    path.chmod(0o644)


def make_config(tmp_path: Path, keys: Keys, **overrides: object) -> AgentConfig:
    tmp_path.mkdir(parents=True, exist_ok=True)
    root = tmp_path / "run"
    root.mkdir(exist_ok=True)
    secrets_root = tmp_path / "secrets"
    secrets_root.mkdir(exist_ok=True)
    secrets_root.chmod(0o711)
    state = tmp_path / "state"
    state.mkdir(exist_ok=True, mode=0o700)
    app = tmp_path / "app"
    app.mkdir(exist_ok=True)
    identity = tmp_path / "identities" / "dispatch.json"
    write_identity(identity)
    bao_dir = tmp_path / "openbao"
    bao_dir.mkdir(exist_ok=True, mode=0o755)
    bao_dir.chmod(0o755)
    values: dict[str, object] = {
        "cp_url": "https://cp.example.invalid",
        "target_ref": TARGET,
        "credential_id": "cred-1",
        "cp_response_public_keys_b64url": (public_b64url(keys.cp),),
        "cp_spki_sha256": ("c" * 64,),
        "state_dir": state,
        "deploy_dir": app,
        "foundation_argv": ("/usr/local/bin/foundation-run",),
        "openbao_url": "http://192.0.2.10:8200",
        "openbao_config_dir": bao_dir,
        "registry_binary": Path("/usr/local/bin/registry"),
        "registry_binary_sha256": "d" * 64,
        "skopeo_path": Path("/usr/bin/skopeo"),
        "skopeo_sha256": "e" * 64,
        "skopeo_policy": Path("/etc/dotmac/skopeo/policy.json"),
        "reconcile_containers": ("erp-app",),
        "bindings_env": (
            ("AUTHORIZATION_VERIFICATION_IDENTITY", "/etc/dotmac/identities/auth.json"),
            ("DISPATCH_VERIFICATION_IDENTITY", str(identity)),
            ("EXECUTION_CONTEXT_MATERIALS", "/etc/dotmac/bindings/context.json"),
            ("HOST_ADMISSION_MATERIALS", "/etc/dotmac/bindings/admission.json"),
            ("RELEASE_EVIDENCE_REPOSITORY", "release-evidence-repo"),
            (
                "RELEASE_EVIDENCE_VERIFICATION_IDENTITY",
                "/etc/dotmac/identities/evidence.json",
            ),
        ),
        "runtime_root": root,
        "secrets_root": secrets_root,
        "secrets_namespace": "erp",
        "secrets_environment": "staging",
        "config_path": tmp_path / "config.toml",
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
            "service": "app",
            "source": "transfer",
            "release_repository": "registry.example.invalid/org/app",
            "release_digest": IMAGE_DIGEST,
            "planned_repository": "127.0.0.1:5000/org/app",
            "planned_digest": IMAGE_DIGEST,
        }
    ]


def make_statement(
    plan_digest: str, *, dispatch_id: str = "dispatch-1", **overrides: object
) -> dict[str, object]:
    raw = DISPATCH_KEY.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    # Control orders dispatches by (execution_sequence, attempt_no); the fixtures
    # follow the dispatch id's trailing number so later ids sort later.
    digits = "".join(c for c in dispatch_id.rsplit("-", 1)[-1] if c.isdigit())
    statement: dict[str, object] = {
        "schema": "dotmac.deployment-dispatch",
        "version": 1,
        "purpose": "deployment_dispatch",
        "dispatch_id": dispatch_id,
        "authorization_id": "auth-1",
        "authorization_plan_id": "plan-1",
        "authorization_control_version": "0.1.0a18",
        "authorization_key_id": "control-authorization-1",
        "authorization_public_key_fingerprint": "sha256:" + "1" * 64,
        "authorization_envelope_digest": "sha256:" + "2" * 64,
        "execution_sequence": int(digits or 1),
        "attempt_no": 1,
        "rollout_ref": "rollout-1",
        "target_id": "target-1",
        "target_ref": TARGET,
        "product_code": "erp",
        "environment": "staging",
        "operation": "deploy",
        "release_ref": "release-1",
        "authorized_images": [],
        "plan_digest": plan_digest,
        "descriptor_digest": "sha256:" + "3" * 64,
        "execution_plan_digest": "sha256:" + "4" * 64,
        "issued_at": "2027-01-06T11:59:00Z",
        "control_version": "0.1.0a18",
        "key_id": DISPATCH_KEY_ID,
        "algorithm": "ed25519",
        "public_key_fingerprint": fingerprint_of(raw),
    }
    statement.update(overrides)
    return statement


def sign_envelope(
    statement: dict[str, object], key: Ed25519PrivateKey = DISPATCH_KEY
) -> dict[str, object]:
    return {
        "statement": statement,
        "signature": encode_b64url(key.sign(canonical_json(statement))),
    }


def make_materials(**overrides: object) -> dict[str, object]:
    """A materials document the way CP serves it: a Control-signed envelope, the
    plan snapshot it binds, and the few CP-supplied fields (ruling R1)."""
    params = overrides.pop("params", None) or base_params()
    app_image = str(overrides.pop("app_image", APP_IMAGE))
    jobs = overrides.pop("jobs", None)
    dispatched = overrides.pop("dispatched_job", None)
    scoped = {n for j in (jobs or []) for n in j["params"]}  # type: ignore[index, union-attr]
    serving = [r for r in params if r["name"] not in scoped]  # type: ignore[attr-defined]
    try:
        digest = render_digest(canonical_render(to_params(serving), app_image))  # type: ignore[arg-type]
    except Refusal:
        digest = "sha256:" + "0" * 64  # a refused render has no honest digest
    binding: dict[str, object] = {
        "deploy_unit_digest": "sha256:" + "5" * 64,
        "product_toml_sha256": "sha256:" + "6" * 64,
        "params_env_digest": overrides.pop("params_env_digest", digest),
        "images": overrides.pop("images", base_images()),
        "lease_ttl_seconds": overrides.pop("lease_ttl_seconds", 900),
        "active_binding_names": overrides.pop("active_binding_names", []),
        "legacy": overrides.pop("legacy", None),
        "companion_order": None,
        "primary_service": overrides.pop("primary_service", "app"),
        "secrets_namespace": overrides.pop("secrets_namespace", "erp"),
        "secrets_environment": overrides.pop("secrets_environment", "staging"),
        "roles": overrides.pop("roles", []),
    }
    lease_max = overrides.pop("lease_max_seconds", None)
    if lease_max is not None:
        binding["lease_max_seconds"] = lease_max
    if jobs is not None:
        binding["jobs"] = jobs
    if dispatched is not None:
        binding["dispatched_job"] = dispatched
    target = str(overrides.pop("target_ref", TARGET))
    product, environment, _host = target.split("/")
    snapshot: dict[str, object] = {
        "deploy_binding": binding,
        "release_ref": "release-1",
        "product_code": product,
        "environment": environment,
        "target_ref": target,
        "plan_purpose": "foundation_execution",
    }
    snapshot.update(overrides.pop("snapshot_extra", {}))  # type: ignore[arg-type]
    plan_digest = plan_digest_of(snapshot)
    document_dispatch_id = str(overrides.pop("dispatch_id", "dispatch-1"))
    statement_fields: dict[str, object] = {
        "dispatch_id": document_dispatch_id,
        "target_ref": target,
        "product_code": product,
        "environment": environment,
        **overrides.pop("statement", {}),  # type: ignore[arg-type]
    }
    statement = make_statement(
        plan_digest,
        dispatch_id=str(statement_fields.pop("dispatch_id")),
        **statement_fields,
    )
    envelope = sign_envelope(statement, overrides.pop("sign_with", DISPATCH_KEY))  # type: ignore[arg-type]
    document: dict[str, object] = {
        "schema": "dotmac.deploy_target_materials/1",
        "pending": True,
        "store_now": wire.utc_stamp(NOW),
        "dispatch_id": document_dispatch_id,
        "target_ref": target,
        "dispatch_envelope": envelope,
        "plan_snapshot": snapshot,
        "params": params,
        "app_image": app_image,
    }
    document.update(overrides)
    return document


class FakeCp:
    """The CP target API as the agent sees it. Verifies requests, signs responses."""

    def __init__(
        self,
        keys: Keys,
        clock: Callable[[], datetime] = lambda: NOW,
        target: str = TARGET,
    ) -> None:
        self.target = target
        self.keys = keys
        self.clock = clock
        self.materials: dict[str, object] | None = None
        self.credential_target_ref = target
        self.credential_id = "cred-1"
        self.observations: list[dict[str, object]] = []
        self.nonces: set[str] = set()
        self.calls: list[tuple[str, str]] = []
        self.paths: list[str] = []
        self.overrides: dict[str, tuple[int, dict[str, object]]] = {}
        self.raw_status: dict[str, int] = {}
        self.store_now_override: str | None = None
        self.tamper_signature = False
        self.omit_signature = False
        self.status_5xx = False
        self.standing = "CLEAN"
        self.expired_run: str | None = None  # CP's lease row, once expired
        self.lease_log: list[tuple[str, dict[str, object]]] = []
        self.handlers: dict[str, Callable[[dict[str, object]], dict[str, object]]] = {
            "lease/standing": lambda _p: {"standing": self.standing}
            | ({"dispatch_run": self.expired_run} if self.expired_run else {}),
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
        self.paths.append(path)
        try:
            credential = request_credential_id(headers)
            if credential != self.credential_id:
                return self._reply(
                    method, path, nonce, 401, {"error": "unknown_credential"}
                )
            coords = check_request(
                method=method,
                raw_path=path,
                query_string="",
                headers=headers,
                body=body,
                store_now=wire.utc_stamp(self.clock()),
                credential_target_ref=self.credential_target_ref,
                looked_up_credential_id=credential,
            )
        except RequestRefused as refused:
            return self._reply(method, path, nonce, 400, {"error": refused.code})
        public = Ed25519PublicKey.from_public_bytes(
            self.keys.agent.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        )
        try:
            public.verify(
                wire.decode_b64url(coords.signature_b64u, length=64) or b"",
                coords.signed_message,
            )
        except InvalidSignature:
            return self._reply(method, path, nonce, 401, {"error": "bad_signature"})
        if coords.nonce_ledger_key in self.nonces:
            return self._reply(method, path, nonce, 403, {"error": "nonce_replay"})
        self.nonces.add(coords.nonce_ledger_key)
        endpoint = coords.route
        self.calls.append((method, endpoint))
        if endpoint in self.raw_status:
            return Response(self.raw_status[endpoint], {}, b"")
        if self.status_5xx:
            return Response(503, {}, b"upstream")
        if endpoint in self.overrides:
            status, doc = self.overrides[endpoint]
            return self._reply(method, path, nonce, status, doc)
        payload = json.loads(body) if body else {}
        if endpoint == "materials":
            doc = self.materials or {
                "schema": "dotmac.deploy_target_materials/1",
                "pending": False,
                "target_ref": self.target,
            }
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
        headers = {wire.H_RESPONSE_SIGNATURE.lower(): encode_b64url(signature)}
        if self.tamper_signature:
            headers = {wire.H_RESPONSE_SIGNATURE.lower(): encode_b64url(b"x" * 64)}
        if self.omit_signature:
            headers = {}
        return Response(status, headers, body)


class FakeBaoTransport:
    """OpenBao as the delivery identity sees it. Records paths and bodies, never values."""

    SECRET = "secret-id-value-for-tests"
    TOKEN = "delivery-token-1"

    def __init__(self) -> None:
        self.paths: list[str] = []
        self.requests: list[tuple[str, str, dict[str, str], dict[str, object]]] = []
        self.minted: dict[str, str] = {}
        self.counter = 0
        self.namespace = "staging"
        self.fail_lookup = False
        self.lookup_path: str | None = None
        self.fail_destroy = False
        self.deny_login = False  # the DELIVERY identity itself is refused
        self.deny_mint: str | None = None  # a substring of the mint path to 403
        self.revoked = False

    def request(
        self, method: str, path: str, headers: Mapping[str, str], body: bytes
    ) -> Response:
        self.paths.append(path)
        payload = json.loads(body) if body else {}
        self.requests.append((method, path, dict(headers), payload))
        assert headers["X-Vault-Namespace"] == self.namespace
        if path == "/v1/auth/approle-delivery/login":
            if self.deny_login:
                return Response(403, {}, b"{}")
            assert set(payload) == {"role_id", "secret_id"}
            return self._ok({"auth": {"client_token": self.TOKEN}})
        if path == "/v1/auth/token/revoke-self":
            assert headers["X-Vault-Token"] == self.TOKEN
            self.revoked = True
            return Response(204, {}, b"")
        if path.endswith("/secret-id") and "/role/" in path:
            assert headers["X-Vault-Token"] == self.TOKEN
            assert headers["X-Vault-Wrap-TTL"]
            if self.deny_mint and self.deny_mint in path:
                return Response(403, {}, b"{}")
            assert set(payload) <= {"metadata", "ttl", "num_uses"}
            self.counter += 1
            wrap = f"wrap-{self.counter}"
            self.minted[wrap] = path.removeprefix("/v1/")
            return self._ok({"wrap_info": {"token": wrap}})
        if path == "/v1/sys/wrapping/lookup":
            if self.fail_lookup:
                return Response(400, {}, b"{}")
            minted = self.minted.get(str(payload["token"]), "")
            return self._ok({"data": {"creation_path": self.lookup_path or minted}})
        if path == "/v1/sys/wrapping/unwrap":
            assert headers["X-Vault-Token"] in self.minted
            return self._ok(
                {
                    "data": {
                        "secret_id": self.SECRET,
                        "secret_id_accessor": f"accessor-{headers['X-Vault-Token']}",
                    }
                }
            )
        if path.endswith("/secret-id-accessor/destroy"):
            assert headers["X-Vault-Token"] == self.TOKEN
            if self.fail_destroy:
                return Response(500, {}, b"{}")
            return Response(204, {}, b"")
        return Response(404, {}, b"{}")

    @staticmethod
    def _ok(document: dict[str, object]) -> Response:
        return Response(200, {}, json.dumps(document).encode())


AUTO = object()  # FakeRunner.receipt: build Foundation's receipt for this very run


def foundation_receipt(spec: RunSpec, **extra: object) -> dict[str, object]:
    """A receipt shaped like Foundation #1149's `OperatorJobReceipt.to_document()`."""
    document: dict[str, object] = {
        "schema": "dotmac.foundation.operator-job-receipt/1",
        "dispatch_id": spec.dispatch_id,
        "job": spec.job_name,
        "role": "import",
        "exit_code": 0,
        "outcome": "succeeded",
        "reason": "",
        "started_at": "2027-01-06T12:00:00Z",
        "finished_at": "2027-01-06T12:05:00Z",
        "inputs": [{"flag": "--manifest", "sha256": "sha256:" + "7" * 64}],
        "output_volume": "dotmac-job-out-" + spec.dispatch_id,
        "render_invocation": spec.job_render_invocation,
        "container_removed": True,
    }
    document.update(extra)
    return document


class FakeRunner:
    job_entry_available = True  # the real runner says False until Foundation ships it

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
        #: What Foundation's stdout tail holds for a `run_job`: None (nothing), a dict,
        #: or AUTO (a receipt shaped like Foundation's own, naming THIS run).
        self.receipt: dict[str, object] | None | object = AUTO
        self.receipt_error: str | None = None

    def run(self, spec: RunSpec) -> RunResult:
        self.specs.append(spec)
        self.seen_params = spec.params_env.read_bytes()
        self.seen_mode = spec.params_env.stat().st_mode & 0o777
        if self.effect is not None:
            self.effect(spec)
        job = spec.job_name is not None
        receipt = foundation_receipt(spec) if self.receipt is AUTO else self.receipt
        return RunResult(
            None if self.timed_out else self.exit_code,
            self.timed_out,
            receipt if job else None,  # type: ignore[arg-type]
            (self.receipt_error or "missing") if job and receipt is None else None,
        )


class FakeTransfer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.error: Refusal | None = None

    def transfer_all(self, images, *, ttl_seconds):  # type: ignore[no-untyped-def]
        self.calls.append(tuple(i.service for i in images))
        if self.error is not None:
            raise self.error
        return tuple(
            TransferResult(
                i.service,
                "sha256:" + "0" * 64,
                i.planned_reference,
                "application/vnd.oci.image.manifest.v1+json",
                True,
                True,
            )
            for i in images
            if i.source == "transfer"
        )


class FakeCommands:
    """Scripted external commands. `script` maps a verb tuple to a result; the
    verbs may sit anywhere after the executable (global flags come first)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.script: dict[tuple[str, ...], CommandResult] = {}

    def run(self, argv: tuple[str, ...], *, timeout: float) -> CommandResult:
        self.calls.append(argv)
        rest = argv[1:]
        for verbs, result in self.script.items():
            width = len(verbs)
            if any(rest[i : i + width] == verbs for i in range(len(rest) - width + 1)):
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
    systemctl: FakeCommands
    epoch: list[float]


def prepare_role(rig: Rig, role: str, secrets_namespace: str | None = None) -> None:
    """What enrolment does: tmpfiles.d makes `/run/dotmac/<secrets_namespace>/<role>`
    (traverse-only) and the kit installs the authored config."""
    namespace = secrets_namespace or rig.config.secrets_namespace
    rig.config.secrets_root.mkdir(parents=True, exist_ok=True)
    rig.config.secrets_root.chmod(0o711)
    for directory in (rig.config.secrets_root / namespace, rig.config.role_dir(role)):
        directory.mkdir(exist_ok=True)
        directory.chmod(0o711)
    authored = rig.config.openbao_config_dir / f"{namespace}-{role}.hcl"
    authored.write_text(AUTHORED)
    authored.chmod(0o644)


def make_rig(
    tmp_path: Path,
    *,
    runner: FakeRunner | None = None,
    environ: dict[str, str] | None = None,
    target_ref: str = TARGET,
    **config_overrides: object,
) -> Rig:
    keys = Keys()
    config = make_config(tmp_path, keys, target_ref=target_ref, **config_overrides)
    cp = FakeCp(keys, target=target_ref)
    bao = FakeBaoTransport()
    runner = runner or FakeRunner()
    env = environ if environ is not None else {}
    if "CREDENTIALS_DIRECTORY" not in env:
        creds = tmp_path / "credentials"
        creds.mkdir(exist_ok=True, mode=0o700)
        (creds / "delivery-role-id").write_text("delivery-role-id-value")
        (creds / "delivery-secret-id").write_text("delivery-secret-id-value")
        (creds / "pop-key").write_bytes(keys.seed())
        env["CREDENTIALS_DIRECTORY"] = str(creds)
    signer = wire.PopSigner.from_seed(config.credential_id, keys.seed())
    client = TargetClient(config, signer, cp, clock=lambda: NOW)
    transfer = FakeTransfer()
    commands = FakeCommands()
    systemctl = FakeCommands()
    systemctl.script[("show",)] = CommandResult(0, UNIT_ACTIVE)
    epoch = [1_800_000_000.0]
    launcher = Launcher(
        config,
        client,
        runner,
        bao,
        LeaseClient(client),
        transfer,
        ReadOnlyCommands(commands),
        systemctl,
        environ=env,
        clock=lambda: NOW,
        root_uid=os.geteuid(),
        epoch=lambda: epoch[0],
    )
    return Rig(
        config,
        keys,
        cp,
        runner,
        bao,
        launcher,
        env,
        transfer,
        commands,
        systemctl,
        epoch,
    )
