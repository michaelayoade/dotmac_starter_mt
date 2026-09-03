"""Discovery makes the ADMIT representable — and every ambiguous shape refuses.

## The defect this closes, and the symmetric trap it must not open

a4's installed CLI could never admit: `authorization_verifier` was an
argparse-namespace attribute nothing ever set, and `_build_effects` a closed
switch over the one in-package provider. Every injection seam was real for an
embedder and decorative for the console script.

Discovery closes that — and a mechanism that makes the admit representable can
also make an UNINTENDED admit representable. So the refusals here get equal
weight with the admit: a second distribution declaring the same group is a
loud stop naming BOTH declarers, never a pick; a declaration that fails to
import or resolves to a look-alike refuses rather than being skipped; and a
bindings object that claims the in-package provider's own name is refused at
construction, because shadowing the built-in swaps effects under an unchanged
command line.

## What is unit-level here and what is not

These tests drive the discovery seam through its injectable `entries`
parameter with fake entry points, so every shape is exercisable. What they
deliberately do NOT prove is that a REAL installed distribution's metadata
reaches this seam — that is the installed end-to-end test's job (item 11),
which installs a real bindings wheel into a venv for the ADMIT half and
removes it for the refusal half, so the same mechanism is shown doing both.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from dotmac_deployment_foundation.errors import PreconditionFailed, SpecError
from dotmac_deployment_foundation.execution_bindings import (
    ENTRY_POINT_GROUP,
    ExecutionBindings,
    declared_provider_names,
    discover_bindings,
)

TARGET = "bindings-target"


class _Verifier:
    def attest(self, material: Any) -> Any:
        return dict(material)


class _Signer:
    def verify(self, *, key_id: str, message: bytes, signature: str) -> bool:
        return signature == "valid"


def _bindings(**overrides: object) -> ExecutionBindings:
    fields: dict[str, object] = {
        "provider": "acme-host",
        "authorization_verifier": _Verifier(),
    }
    fields.update(overrides)
    return ExecutionBindings(**fields)  # type: ignore[arg-type]


class _Entry:
    """A fake importlib.metadata entry point: name, dist, load()."""

    def __init__(
        self,
        name: str = "acme-host",
        dist: str = "acme-deploy-bindings",
        factory: Any = None,
        load_error: Exception | None = None,
    ) -> None:
        self.name = name
        self.dist = argparse.Namespace(name=dist)
        self._factory = factory if factory is not None else (lambda: _bindings())
        self._load_error = load_error

    def load(self) -> Any:
        if self._load_error is not None:
            raise self._load_error
        return self._factory


# ── the typed bindings object refuses every malformed shape ────────────────


def test_a_valid_bindings_object_constructs() -> None:
    assert _bindings().provider == "acme-host"


def test_an_empty_provider_name_is_refused() -> None:
    with pytest.raises(SpecError, match="provider is empty"):
        _bindings(provider="   ")


def test_shadowing_the_in_package_provider_is_refused() -> None:
    """`compose-host` is the facility's own. A distribution claiming it would
    swap effects under an unchanged command line."""
    with pytest.raises(SpecError, match="reserved"):
        _bindings(provider="compose-host")


def test_a_non_callable_effects_factory_is_refused() -> None:
    with pytest.raises(SpecError, match="must be callable"):
        _bindings(build_effects="not callable")


def test_a_verifier_without_attest_is_refused() -> None:
    class Wrong:
        pass

    with pytest.raises(SpecError, match="AuthorizationVerifier"):
        _bindings(authorization_verifier=Wrong())


def test_a_signature_verifier_without_verify_is_refused() -> None:
    class Wrong:
        pass

    with pytest.raises(SpecError, match="SignatureVerifier"):
        _bindings(evidence_verifier=Wrong())


def test_a_policy_of_the_wrong_type_is_refused() -> None:
    with pytest.raises(SpecError, match="TrustPolicy"):
        _bindings(evidence_policy={"repository": "x"})


def test_bindings_carrying_nothing_are_refused() -> None:
    """An empty declaration cannot help the CLI admit and can only mislead."""
    with pytest.raises(SpecError, match="no injectable"):
        ExecutionBindings(provider="acme-host")


# ── discovery: zero, one, many ─────────────────────────────────────────────


def test_zero_declarations_is_none_not_an_error() -> None:
    assert discover_bindings(entries=[]) is None


def test_one_declaration_is_loaded_and_typed() -> None:
    found = discover_bindings(entries=[_Entry()])
    assert isinstance(found, ExecutionBindings)
    assert found.provider == "acme-host"


def test_two_declarations_refuse_naming_both() -> None:
    """THE SYMMETRIC TRAP. A malicious or accidental second distribution
    declaring the same entry point must be a refusal — and the refusal must
    name BOTH declarers, so the operator removes the right one instead of
    guessing which won an iteration-order lottery."""
    with pytest.raises(PreconditionFailed) as caught:
        discover_bindings(
            entries=[
                _Entry(name="acme-host", dist="acme-deploy-bindings"),
                _Entry(name="rival-host", dist="rival-bindings"),
            ]
        )
    message = str(caught.value)
    assert "acme-deploy-bindings:acme-host" in message
    assert "rival-bindings:rival-host" in message


def test_a_declaration_that_fails_to_import_refuses_not_skips() -> None:
    with pytest.raises(PreconditionFailed, match="failed to import"):
        discover_bindings(
            entries=[_Entry(load_error=ImportError("missing native dep"))]
        )


def test_a_factory_that_raises_refuses_naming_the_distribution() -> None:
    def broken() -> None:
        raise RuntimeError("keys unreadable")

    with pytest.raises(PreconditionFailed, match="acme-deploy-bindings"):
        discover_bindings(entries=[_Entry(factory=broken)])


def test_a_look_alike_result_is_refused() -> None:
    """A duck-typed object with the right attributes is exactly what the typed
    contract exists to refuse — same rule as VerifiedAuthorization."""

    class LookAlike:
        provider = "acme-host"
        authorization_verifier = _Verifier()

    with pytest.raises(PreconditionFailed, match="not ExecutionBindings"):
        discover_bindings(entries=[_Entry(factory=lambda: LookAlike())])


def test_a_name_answering_differently_than_declared_is_refused() -> None:
    """The entry point's NAME is what `--provider` offered before anything was
    imported. Bindings answering to a different name were selected by nobody."""
    with pytest.raises(PreconditionFailed, match="selected by nobody"):
        discover_bindings(
            entries=[
                _Entry(name="acme-host", factory=lambda: _bindings(provider="other"))
            ]
        )


# ── name enumeration imports nothing ───────────────────────────────────────


def test_provider_names_come_from_metadata_without_loading() -> None:
    """`validate` and a dry run must not import assembly code. An entry whose
    load() raises still enumerates, which proves no load happened."""
    exploding = _Entry(load_error=ImportError("must never be raised"))
    assert declared_provider_names(entries=[exploding, _Entry(name="zeta")]) == (
        "acme-host",
        "zeta",
    )


def test_a_provider_removed_after_install_refuses_naming_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The usage refusal names the repair, not merely an invalid choice."""
    from dotmac_deployment_foundation import cli

    monkeypatch.setattr(cli, "_declared_provider_names", lambda: ())
    with pytest.raises(argparse.ArgumentTypeError) as caught:
        cli._provider_name("removed-provider")
    assert ENTRY_POINT_GROUP in str(caught.value)
    assert "removed-provider" in str(caught.value)


# ── the CLI consumes what discovery found ──────────────────────────────────


def _receipt_file(tmp_path: Path, spec: Any) -> Path:
    from tests.unit.test_deployment_foundation_execution_seam import _receipt

    now = datetime.now(UTC)
    receipt = tmp_path / "receipt.json"
    receipt.write_text(
        json.dumps(
            _receipt(
                target_ref=TARGET,
                descriptor_digest=spec.to_canonical_document().sha256_digest(),
                approved_at=(now - timedelta(hours=1))
                .isoformat()
                .replace("+00:00", "Z"),
                expires_at=(now + timedelta(hours=1))
                .isoformat()
                .replace("+00:00", "Z"),
            ).as_document()
        ),
        encoding="utf-8",
    )
    return receipt


def test_the_discovered_verifier_makes_the_grant_reachable(tmp_path: Path) -> None:
    """The ADMIT the a4 CLI could not represent, at the unit level: the same
    `_require_grant` that refuses with no bindings issues a grant when
    discovery supplies the verifier. Nothing else differs."""
    from dotmac_deployment_foundation.cli import _require_grant
    from dotmac_deployment_foundation.spec import ProductDeploymentSpec

    from tests.unit.test_deployment_foundation_execution_seam import _descriptor

    descriptor = _descriptor(tmp_path)
    spec = ProductDeploymentSpec.load(descriptor)
    receipt = _receipt_file(tmp_path, spec)
    args = argparse.Namespace(
        target=TARGET, authorization=str(receipt), authorization_verifier=None
    )

    with pytest.raises(PreconditionFailed) as caught:
        _require_grant(args, spec, "deploy", bindings=None)
    assert ENTRY_POINT_GROUP in str(caught.value), (
        "the refusal must say what was looked for, or an operator cannot know "
        "the fix is to install the assembly's bindings distribution"
    )

    grant = _require_grant(args, spec, "deploy", bindings=_bindings())
    assert grant.operation == "deploy"
    assert grant.target == TARGET


def test_an_embedders_verifier_wins_over_discovery(tmp_path: Path) -> None:
    """An embedder that set the namespace attribute IS the assembly; a
    discovered distribution must not shadow it."""
    from dotmac_deployment_foundation.cli import _require_grant
    from dotmac_deployment_foundation.spec import ProductDeploymentSpec

    from tests.unit.test_deployment_foundation_execution_seam import _descriptor

    class Refusing:
        def attest(self, material: Any) -> Any:
            raise PreconditionFailed("the embedder's verifier ran")

    descriptor = _descriptor(tmp_path)
    spec = ProductDeploymentSpec.load(descriptor)
    receipt = _receipt_file(tmp_path, spec)
    args = argparse.Namespace(
        target=TARGET, authorization=str(receipt), authorization_verifier=Refusing()
    )
    with pytest.raises(PreconditionFailed, match="the embedder's verifier ran"):
        _require_grant(args, spec, "deploy", bindings=_bindings())


def test_a_discovered_provider_builds_the_assemblys_effects(tmp_path: Path) -> None:
    from dotmac_deployment_foundation.cli import _build_effects
    from dotmac_deployment_foundation.spec import ProductDeploymentSpec

    from tests.unit.test_deployment_foundation_execution_seam import _descriptor

    sentinel = object()
    calls: list[tuple[Any, Path]] = []

    def factory(spec: Any, deploy_dir: Path) -> Any:
        calls.append((spec, deploy_dir))
        return sentinel

    spec = ProductDeploymentSpec.load(_descriptor(tmp_path))
    args = argparse.Namespace(provider="acme-host", deploy_dir=str(tmp_path))
    built = _build_effects(spec, args, bindings=_bindings(build_effects=factory))
    assert built is sentinel
    assert calls == [(spec, tmp_path)]


def test_a_provider_with_no_bindings_behind_it_refuses(tmp_path: Path) -> None:
    from dotmac_deployment_foundation.cli import _build_effects
    from dotmac_deployment_foundation.spec import ProductDeploymentSpec

    from tests.unit.test_deployment_foundation_execution_seam import _descriptor

    spec = ProductDeploymentSpec.load(_descriptor(tmp_path))
    args = argparse.Namespace(provider="acme-host", deploy_dir=str(tmp_path))
    with pytest.raises(PreconditionFailed, match="no loaded execution bindings"):
        _build_effects(spec, args, bindings=None)


def test_verifier_only_bindings_cannot_be_selected_as_a_provider(
    tmp_path: Path,
) -> None:
    from dotmac_deployment_foundation.cli import _build_effects
    from dotmac_deployment_foundation.spec import ProductDeploymentSpec

    from tests.unit.test_deployment_foundation_execution_seam import _descriptor

    spec = ProductDeploymentSpec.load(_descriptor(tmp_path))
    args = argparse.Namespace(provider="acme-host", deploy_dir=str(tmp_path))
    with pytest.raises(PreconditionFailed, match="no effects factory"):
        _build_effects(spec, args, bindings=_bindings(build_effects=None))
