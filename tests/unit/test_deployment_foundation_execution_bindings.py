"""Execution bindings fix V3 trust at assembly installation, not per request.

Discovery keeps its ambiguity and provider-name refusals. These unit tests
prove the CLI consumes a discovered V3 provider while ignoring an argparse
verifier/provider field. The installed wheel's positive execution remains
held until a ratified CP pair-provider composition exists; a V1 receipt or
test-only verifier cannot make the release lane admit.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest
from dotmac_deployment_foundation.errors import PreconditionFailed, SpecError
from dotmac_deployment_foundation.execution_bindings import (
    ENTRY_POINT_GROUP,
    ExecutionBindings,
    declared_provider_names,
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


# ── discovery: MOVED, and deliberately not duplicated here ─────────────────
#
# Seven tests lived here covering zero/one/many declarations and the five
# refusals. They now live in `test_deployment_foundation_discovery.py`, which
# proves EVERY consumer of `discovery.discover_one` against all five — on typed
# codes rather than on prose, and over a consumer list derived from the package
# by an AST sweep rather than hand-maintained.
#
# They are removed rather than left alongside, because this whole change is
# about not keeping a second authority over one question. Two suites asserting
# the same refusals is the test-side shape of the same defect: they agree until
# one is updated and the other is not, and then the stale one still passes.
#
# What stays in THIS file is what is specific to `ExecutionBindings` — the
# typed object's own construction refusals above, the provider-name enumeration
# below, and the CLI actually consuming what discovery found.

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
    receipt = tmp_path / "control-v2-pair.json"
    receipt.write_text(
        json.dumps(
            {
                "authorization_material": {"kind": "authorization"},
                "dispatch_material": {"kind": "dispatch"},
            }
        ),
        encoding="utf-8",
    )
    return receipt


def test_the_discovered_verifier_makes_the_grant_reachable(tmp_path: Path) -> None:
    """Only startup-installed V3 provider composition makes the pair usable."""
    from dotmac_deployment_foundation.cli import _require_grant
    from dotmac_deployment_foundation.spec import ProductDeploymentSpec

    from tests.unit.foundation_v3_support import provider_for
    from tests.unit.test_deployment_foundation_execution_seam import (
        _descriptor,
        _subject,
    )

    descriptor = _descriptor(tmp_path)
    spec = ProductDeploymentSpec.load(descriptor)
    _, plan = _subject(tmp_path)
    receipt = _receipt_file(tmp_path, spec)
    args = argparse.Namespace(target=plan.target, authorization=str(receipt))

    with pytest.raises(PreconditionFailed, match="startup-fixed"):
        _require_grant(args, spec, "deploy", execution_plan=plan, bindings=None)

    grant = _require_grant(
        args,
        spec,
        "deploy",
        execution_plan=plan,
        bindings=_bindings(authorization_v3_provider=provider_for(spec, plan)),
    )
    assert grant.operation == "deploy"
    assert grant.target == plan.target


def test_request_selected_verifier_cannot_replace_fixed_provider(
    tmp_path: Path,
) -> None:
    """An argparse namespace cannot select the V3 attester or clock."""
    from dotmac_deployment_foundation.cli import _require_grant
    from dotmac_deployment_foundation.spec import ProductDeploymentSpec

    from tests.unit.foundation_v3_support import provider_for
    from tests.unit.test_deployment_foundation_execution_seam import (
        _descriptor,
        _subject,
    )

    class Refusing:
        def attest(self, material: Any) -> Any:
            raise AssertionError("request-selected verifier ran")

    descriptor = _descriptor(tmp_path)
    spec = ProductDeploymentSpec.load(descriptor)
    _, plan = _subject(tmp_path)
    receipt = _receipt_file(tmp_path, spec)
    args = argparse.Namespace(
        target=plan.target,
        authorization=str(receipt),
        authorization_verifier=Refusing(),
    )
    grant = _require_grant(
        args,
        spec,
        "deploy",
        execution_plan=plan,
        bindings=_bindings(authorization_v3_provider=provider_for(spec, plan)),
    )
    assert grant.target == plan.target


def test_cli_load_bindings_ignores_request_selected_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dotmac_deployment_foundation import cli, execution_bindings

    fixed = _bindings()
    monkeypatch.setattr(execution_bindings, "discover_bindings", lambda: fixed)
    args = argparse.Namespace(bindings=object(), authorization_v3_provider=object())
    assert cli._load_bindings(args) is fixed


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
