"""`DeploymentProvenance.v1` — the six bindings, and the refusals that matter.

The canonical document answers "what was authorized". This file covers the
question after it: is the thing about to execute the thing that was authorized,
and can a reader months later say what it was built from?

Three properties carry the file.

**The receipt binds by VALUE, never by import.** `dotmac-deployment-control`
owns authorization and is a stateful SQLAlchemy module; this facility declares
zero runtime dependencies. `test_provenance_imports_nothing_outside_the_standard_
library` holds both at once by reading the module's own AST, so a future
"simplification" that imports Control fails here as well as in the
classification guard.

**A tag is not identity.** An image tag is a mutable pointer — it can be moved
to different bytes after the approval and before the deploy, and the record
would still read as true. Every refusal in the digest group has a matching
accept, so the guard cannot pass by refusing everything.

**The mismatch refusal is not an authorization decision.** Foundation never
judges whether an approval should have been granted; it refuses to execute
something OTHER than what was authorized. The two are easy to conflate in code
review, so the test that covers it says which one it is asserting.
"""

from __future__ import annotations

import ast
import hashlib
import json
import pathlib
import sys

import pytest
from dotmac_deployment_foundation.errors import SpecError
from dotmac_deployment_foundation.provenance import (
    PROVENANCE_SCHEMA,
    AuthorizationReceipt,
    DeploymentProvenanceV1,
    build_provenance,
    normalize_digest,
)
from dotmac_deployment_foundation.spec import ProductDeploymentSpec
from dotmac_deployment_foundation.version import VERSION

_MANIFEST_DIGEST = "sha256:" + "a" * 64
_IMAGE = f"registry.example.com/acme/app@sha256:{'b' * 64}"
_REVISION = "c" * 40

_DESCRIPTOR = f"""
schema = "ProductDeploymentSpec.v1"
product = "acme"
environment = "prod"

[assembly]
manifest_path = "deploy/product.toml"
manifest_digest = "{_MANIFEST_DIGEST}"

[image]
reference = "{_IMAGE}"
source_revision = "{_REVISION}"

[runtime_materials]
names = ["DATABASE_URL"]

[migration]
command = ["alembic", "upgrade", "heads"]
heads_command = ["alembic", "current"]
owner_material = "MIGRATION_DATABASE_URL"
expected_heads = ["abc123"]
compatibility = "online"
lock_timeout_seconds = 300

[rollout]
stability_window_seconds = 240
rollback_images_retained = 3

[backup]
[[backup.datasets]]
code = "primary"
kind = "postgres"
material = "BACKUP_DATABASE_URL"
retention_days = 30
verify = ["schema", "row_counts"]

[[roles]]
code = "app"
command = ["python", "-m", "app"]
replicas = 2
materials = ["DATABASE_URL"]

[roles.resources]
cpus = "0.5"
memory = "256m"

[roles.health.ready]
path = "/readyz"
port = 8003

[[roles]]
code = "worker"
command = ["python", "-m", "worker"]
replicas = 1
materials = ["DATABASE_URL"]

[roles.resources]
cpus = "0.5"
memory = "256m"

[roles.worker]
kind = "celery"
ping_command = ["celery", "-A", "worker", "inspect", "ping"]
heartbeat_max_age_seconds = 120
max_backlog = 1000
"""

_RENDERED = {
    "docker-compose.yml": "sha256:" + "1" * 64,
    "alerts.rules.yml": "sha256:" + "2" * 64,
}
_IMAGES = {"app": _IMAGE, "worker": _IMAGE}
_ROSTER = ("app", "worker")


@pytest.fixture(scope="module")
def spec() -> ProductDeploymentSpec:
    return ProductDeploymentSpec.loads(_DESCRIPTOR, source="<provenance-fixture>")


@pytest.fixture(scope="module")
def descriptor_digest(spec: ProductDeploymentSpec) -> str:
    return spec.to_canonical_document().sha256_digest()


def _receipt(digest: str, **overrides: object) -> AuthorizationReceipt:
    fields: dict[str, object] = {
        "plan_id": "0f5f3a2c-1111-4444-8888-abcdefabcdef",
        "target_ref": "acme-prod-1",
        "descriptor_digest": digest,
        "policy_code": "deployment.production",
        "policy_version": 3,
        "decision_ref": "approvals:decision:9182",
        "approved_at": "2026-08-30T10:15:00Z",
        "control_version": "0.1.0a4",
    }
    fields.update(overrides)
    return AuthorizationReceipt(**fields)  # type: ignore[arg-type]


def _build(
    spec: ProductDeploymentSpec, digest: str, **overrides: object
) -> DeploymentProvenanceV1:
    kwargs: dict[str, object] = {
        "rendered_digests": _RENDERED,
        "image_digests": _IMAGES,
        "source_revision": _REVISION,
        "service_roster": _ROSTER,
        "authorization": _receipt(digest),
    }
    kwargs.update(overrides)
    return build_provenance(spec, **kwargs)  # type: ignore[arg-type]


# ── the happy path, so every refusal below has a working control ────────────


def test_the_six_bindings_are_all_present_and_named(
    spec: ProductDeploymentSpec, descriptor_digest: str
) -> None:
    """Each is a separate binding because each has its own failure mode; a
    record missing one of them cannot answer the question it exists for."""
    record = _build(spec, descriptor_digest)
    content = record.content
    assert content["schema"] == PROVENANCE_SCHEMA
    assert content["foundation_version"] == VERSION
    assert content["descriptor_digest"] == descriptor_digest
    assert content["rendered_digests"] == dict(_RENDERED)
    assert content["image_digests"] == dict(_IMAGES)
    assert content["source_revision"] == _REVISION
    assert content["service_roster"] == ["app", "worker"]
    assert content["authorization"]["decision_ref"] == "approvals:decision:9182"


def test_the_digest_is_the_sha256_of_exactly_the_canonical_bytes(
    spec: ProductDeploymentSpec, descriptor_digest: str
) -> None:
    record = _build(spec, descriptor_digest)
    expected = hashlib.sha256(record.canonical_bytes()).hexdigest()
    assert record.sha256_digest() == f"sha256:{expected}"


def test_the_canonical_bytes_are_sorted_compact_utf8(
    spec: ProductDeploymentSpec, descriptor_digest: str
) -> None:
    raw = _build(spec, descriptor_digest).canonical_bytes()
    assert raw == json.dumps(
        json.loads(raw), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def test_the_same_inputs_produce_the_same_bytes_every_time(
    spec: ProductDeploymentSpec, descriptor_digest: str
) -> None:
    """Nothing reads a clock, an environment variable, a filesystem or a
    network — so two runs that disagree disagree about the deployment rather
    than about the recording of it."""
    assert (
        _build(spec, descriptor_digest).canonical_bytes()
        == _build(spec, descriptor_digest).canonical_bytes()
    )


# ── the authorization binding ───────────────────────────────────────────────


def test_a_receipt_for_a_different_descriptor_is_refused(
    spec: ProductDeploymentSpec,
) -> None:
    """This is NOT Foundation judging the approval — it is refusing to execute
    something other than what was authorized, which is its own business.

    The distinction is the whole ownership line, so it is asserted on the
    message rather than only on the exception type.
    """
    with pytest.raises(SpecError) as excinfo:
        _build(spec, "sha256:" + "9" * 64)
    message = str(excinfo.value)
    assert "does not cover this descriptor" in message
    assert "not judging the approval" in message


def test_the_receipt_binds_in_either_digest_spelling(
    spec: ProductDeploymentSpec, descriptor_digest: str
) -> None:
    """Control's `plan_digest` is BARE hex and this facility emits the prefixed
    form. If normalization were missing, the mismatch would read as a security
    refusal while actually being a formatting bug — the kind of alarm that gets
    suppressed rather than investigated."""
    bare = descriptor_digest.removeprefix("sha256:")
    assert bare != descriptor_digest
    record = _build(spec, descriptor_digest, authorization=_receipt(bare))
    assert record.content["authorization"]["descriptor_digest"] == descriptor_digest


def test_normalize_digest_accepts_both_forms_and_refuses_a_non_digest() -> None:
    hexdigest = "d" * 64
    assert normalize_digest(hexdigest, where="t") == f"sha256:{hexdigest}"
    assert normalize_digest(f"sha256:{hexdigest}", where="t") == f"sha256:{hexdigest}"
    upper = f"SHA256:{hexdigest.upper()}"
    assert normalize_digest(upper, where="t") == f"sha256:{hexdigest}"
    for bad in ("", "sha256:", "deadbeef", "sha512:" + "d" * 64, "z" * 64):
        with pytest.raises(SpecError):
            normalize_digest(bad, where="t")


@pytest.mark.parametrize(
    "field",
    ["plan_id", "target_ref", "policy_code", "decision_ref", "approved_at"],
)
def test_a_structurally_incomplete_receipt_is_refused(
    descriptor_digest: str, field: str
) -> None:
    """Refusing an EMPTY decision_ref checks that the value is a receipt at
    all. Refusing an UNFAVOURABLE decision would be evaluating authorization,
    which belongs to Control — and is absent here on purpose."""
    with pytest.raises(SpecError):
        _receipt(descriptor_digest, **{field: "   "})


def test_a_receipt_must_name_the_control_version_that_issued_it(
    descriptor_digest: str,
) -> None:
    """A receipt is only as meaningful as the rules that produced it, and those
    are versioned — currently mid-move to their own repository."""
    with pytest.raises(SpecError, match="control_version"):
        _receipt(descriptor_digest, control_version="")


# ── image digests: a tag is a mutable pointer ───────────────────────────────


@pytest.mark.parametrize(
    "reference",
    [
        "registry.example.com/acme/app:1.4.2",
        "registry.example.com/acme/app",
        "registry.example.com/acme/app:latest",
        "acme/app@sha256:tooshort",
        "acme/app@md5:" + "b" * 32,
    ],
)
def test_an_image_that_is_not_digest_pinned_is_refused(
    spec: ProductDeploymentSpec, descriptor_digest: str, reference: str
) -> None:
    with pytest.raises(SpecError, match="digest-pinned|MUTABLE POINTER"):
        _build(
            spec,
            descriptor_digest,
            image_digests={"app": reference, "worker": _IMAGE},
        )


def test_a_tagged_reference_that_also_carries_a_digest_is_accepted(
    spec: ProductDeploymentSpec, descriptor_digest: str
) -> None:
    """The negative control for the group above: what is refused is a reference
    with no digest, not the presence of a human-readable tag."""
    both = f"registry.example.com/acme/app:1.4.2@sha256:{'b' * 64}"
    record = _build(
        spec, descriptor_digest, image_digests={"app": both, "worker": _IMAGE}
    )
    assert record.content["image_digests"]["app"] == both


def test_image_digests_must_cover_exactly_the_roles(
    spec: ProductDeploymentSpec, descriptor_digest: str
) -> None:
    with pytest.raises(SpecError, match="missing"):
        _build(spec, descriptor_digest, image_digests={"app": _IMAGE})
    with pytest.raises(SpecError, match="unexpected"):
        _build(
            spec,
            descriptor_digest,
            image_digests={**_IMAGES, "ghost": _IMAGE},
        )


# ── source revision ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "revision", ["main", "HEAD", "c" * 12, "", "C" * 40 + "0", "z" * 40]
)
def test_a_revision_that_is_not_a_full_commit_is_refused(
    spec: ProductDeploymentSpec, descriptor_digest: str, revision: str
) -> None:
    """A branch moves, and an abbreviated hash is a lookup that can become
    ambiguous as the repository grows. Neither identifies the source later."""
    with pytest.raises(SpecError, match="not a full commit"):
        _build(spec, descriptor_digest, source_revision=revision)


def test_an_uppercase_commit_is_accepted_and_normalized(
    spec: ProductDeploymentSpec, descriptor_digest: str
) -> None:
    """Negative control for the group above — case is spelling, not identity."""
    record = _build(spec, descriptor_digest, source_revision=_REVISION.upper())
    assert record.source_revision == _REVISION


# ── the service roster ──────────────────────────────────────────────────────


def test_the_roster_must_equal_the_descriptors_roles(
    spec: ProductDeploymentSpec, descriptor_digest: str
) -> None:
    """A subset would let the record describe a deployment that quietly dropped
    a worker; a superset would let it claim a service never composed."""
    with pytest.raises(SpecError, match="missing"):
        _build(spec, descriptor_digest, service_roster=("app",))
    with pytest.raises(SpecError, match="unexpected"):
        _build(spec, descriptor_digest, service_roster=("app", "worker", "ghost"))


def test_the_roster_is_order_insensitive_and_recorded_sorted(
    spec: ProductDeploymentSpec, descriptor_digest: str
) -> None:
    record = _build(spec, descriptor_digest, service_roster=("worker", "app"))
    assert record.service_roster == ("app", "worker")


# ── rendered assets ─────────────────────────────────────────────────────────


def test_rendered_digests_may_not_be_empty(
    spec: ProductDeploymentSpec, descriptor_digest: str
) -> None:
    """A record over no rendered bytes cannot detect a hand-edit, which is the
    specific failure `render --check` exists to prevent."""
    with pytest.raises(SpecError, match="rendered_digests is empty"):
        _build(spec, descriptor_digest, rendered_digests={})


def test_a_rendered_digest_is_normalized_and_a_bad_one_is_refused(
    spec: ProductDeploymentSpec, descriptor_digest: str
) -> None:
    record = _build(
        spec,
        descriptor_digest,
        rendered_digests={"docker-compose.yml": "e" * 64},
    )
    expected = "sha256:" + "e" * 64
    assert record.content["rendered_digests"]["docker-compose.yml"] == expected
    with pytest.raises(SpecError):
        _build(
            spec,
            descriptor_digest,
            rendered_digests={"docker-compose.yml": "not-a-digest"},
        )


# ── the ownership line, held statically ─────────────────────────────────────


def _module_path() -> pathlib.Path:
    import dotmac_deployment_foundation.provenance as module

    return pathlib.Path(module.__file__)


def test_provenance_imports_nothing_outside_the_standard_library() -> None:
    """The facility declares ZERO runtime dependencies, and binding the receipt
    by value rather than by import is what keeps that true while still tying
    execution to Control's authorization."""
    tree = ast.parse(_module_path().read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            imported.append(node.module or "")
    outside = []
    for name in imported:
        root = name.split(".")[0]
        if root not in sys.stdlib_module_names and name != "__future__":
            outside.append(name)
    assert not outside, f"provenance grew a runtime dependency: {outside}"


def test_provenance_never_reaches_for_deployment_control() -> None:
    """A planted-defect proof would be the wrong shape here — the property is
    the ABSENCE of a name, so the check is over the source text and its
    sensitivity comes from the two spellings being searched at once."""
    source = _module_path().read_text(encoding="utf-8")
    code = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("#")
    )
    tree = ast.parse(code)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        for name in names:
            assert "deployment_control" not in name, (
                f"provenance imports {name!r}. The receipt is bound by VALUE so "
                "that a zero-dependency build runner never depends on a "
                "stateful SQLAlchemy module, and so that this facility never "
                "reaches into another owner's authorization state"
            )
