"""One discovery core, and EVERY consumer proven against all five refusals.

## What the extraction is for

`discover_bindings` owned five refusals in forty lines. A second kind of
declaration needs the same five, and the obvious move — a second function shaped
like the first — is a SECOND AUTHORITY over one question. The failure mode is
not that the copy is wrong when written. **Copies agree right up until they
don't:** one gets a refusal tightened or a new ambiguous shape closed, the other
does not, and the divergence is invisible because both still look correct.

This repository fixed that exact defect one layer down, twice in one evening:
`AuthorizationReceipt.__post_init__` restated `("deploy", "rollback")` instead of
reading `OPERATIONS`, so widening the vocabulary left one layer saying three and
the next saying two. A restatement of a rule is a copy of it.

## Why this file derives its consumer list instead of listing it

The extraction is only worth anything if every consumer actually gets all five
refusals, and "it calls the shared function, so it inherits them" is exactly the
kind of reasoning that is true until somebody passes a permissive argument.

So `CONSUMERS` is checked against an AST sweep of the package for callers of
`discover_one`. A consumer added without an entry here **fails the build**. That
matters concretely right now: this core has ONE consumer today, and the
`ApplicationFoundationProfile` discovery is the second. That second consumer
cannot land without proving itself against all five, because
`test_the_consumer_table_covers_every_caller_of_discover_one` will fail until it
is in this table — which is the difference between a promise and a ratchet.

**One consumer is stated as a limitation, not hidden.** A generic with a single
caller is a generalization asserted rather than proven; `test_the_core_has_a_
consumer_count_this_file_admits_to` records the count so the day it changes is a
diff somebody reads.
"""

from __future__ import annotations

import argparse
import ast
import pathlib
from typing import Any

import pytest
from dotmac_deployment_foundation import discovery
from dotmac_deployment_foundation.application_profile import (
    ApplicationFoundationProfile,
    ConcernBinding,
    FoundationConcern,
    discover_profile,
)
from dotmac_deployment_foundation.discovery import (
    DISCOVERY_AMBIGUOUS,
    DISCOVERY_FACTORY_RAISED,
    DISCOVERY_IMPORT_FAILED,
    DISCOVERY_NAME_MISMATCH,
    DISCOVERY_REFUSALS,
    DISCOVERY_WRONG_TYPE,
    discover_one,
)
from dotmac_deployment_foundation.errors import PreconditionFailed
from dotmac_deployment_foundation.execution_bindings import (
    ExecutionBindings,
    discover_bindings,
)

PACKAGE = (
    pathlib.Path(__file__).resolve().parents[2]
    / "packages/dotmac-deployment-foundation/src/dotmac_deployment_foundation"
)


class _Verifier:
    def attest(self, material: Any) -> Any:
        return dict(material)


class _Entry:
    """A fake importlib.metadata entry point: name, dist, load()."""

    def __init__(
        self,
        name: str,
        factory: Any,
        *,
        dist: str = "acme-bindings",
        load_error: Exception | None = None,
    ) -> None:
        self.name = name
        self.dist = argparse.Namespace(name=dist)
        self._factory = factory
        self._load_error = load_error

    def load(self) -> Any:
        if self._load_error is not None:
            raise self._load_error
        return self._factory


def _bindings(provider: str = "acme-host") -> ExecutionBindings:
    return ExecutionBindings(provider=provider, authorization_verifier=_Verifier())


def _profile(application: str = "acme-host") -> ApplicationFoundationProfile:
    """A complete thirteen-slot profile. Every concern bound to the same
    placeholder, because THIS file is about discovery — what a slot holds is
    `test_deployment_foundation_application_profile.py`'s subject."""
    binding = ConcernBinding(
        implementation="acme-foundation",
        version="1.0.0",
        coordinates="acme-foundation@sha256:" + "b" * 64,
    )
    return ApplicationFoundationProfile(
        application=application,
        slots=dict.fromkeys(FoundationConcern, binding),
    )


class _Consumer:
    """One real caller of `discover_one`, and enough to drive every refusal."""

    def __init__(self, *, module: str, discover, name: str, make, wrong_name) -> None:
        self.module = module
        self.discover = discover
        self.name = name
        self.make = make
        self.wrong_name = wrong_name


#: Every consumer of the core. Checked against an AST sweep below — this is not
#: a hand-maintained list that can quietly fall behind the package.
CONSUMERS: dict[str, _Consumer] = {
    "execution_bindings": _Consumer(
        module="execution_bindings.py",
        discover=discover_bindings,
        name="acme-host",
        make=lambda: _bindings(),
        wrong_name=lambda: _bindings(provider="somebody-else"),
    ),
    "application_profile": _Consumer(
        module="application_profile.py",
        discover=discover_profile,
        name="acme-host",
        make=lambda: _profile(),
        wrong_name=lambda: _profile(application="somebody-else"),
    ),
}


# ── the extent: no consumer escapes this file ───────────────────────────────


def _modules_calling_discover_one() -> set[str]:
    """AST, not grep. A mention in a docstring is not a call, and this file's own
    reasoning about `discover_one` must not read as a consumer."""
    found: set[str] = set()
    for path in sorted(PACKAGE.rglob("*.py")):
        if path.name == "discovery.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            named = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr
                if isinstance(func, ast.Attribute)
                else ""
            )
            if named == "discover_one":
                found.add(path.name)
    return found


def test_the_consumer_table_covers_every_caller_of_discover_one() -> None:
    """DERIVED extent. A new consumer that is not proven against all five
    refusals fails HERE, which is what makes the extraction a ratchet rather
    than an intention. The ApplicationFoundationProfile discovery is the second
    consumer and will trip this until it is added."""
    calling = _modules_calling_discover_one()
    covered = {consumer.module for consumer in CONSUMERS.values()}
    assert calling == covered, (
        f"modules calling discover_one: {sorted(calling)}; modules proven in "
        f"CONSUMERS: {sorted(covered)}. Every consumer is exercised against "
        f"all five refusals ({list(DISCOVERY_REFUSALS)}) or the core has a "
        "caller that never proves it"
    )


def test_the_sweep_would_actually_find_a_caller() -> None:
    """Sensitivity for the sweep itself. An AST walk that matched nothing would
    make the test above pass over an empty set."""
    assert _modules_calling_discover_one(), "the AST sweep found no caller at all"


def test_the_core_has_a_consumer_count_this_file_admits_to() -> None:
    """TWO consumers, which is what makes the extraction proven rather than
    asserted. It shipped with one, recorded as a limitation; the profile
    discovery is the second, and it could not have landed without appearing in
    the table above — the AST sweep failed until it did. The count is recorded
    so a third is a diff somebody reads."""
    assert len(CONSUMERS) == 2


def test_every_refusal_is_covered_by_a_test_in_this_file() -> None:
    """The refusal vocabulary is closed and this file exercises all of it."""
    exercised = {
        DISCOVERY_AMBIGUOUS,
        DISCOVERY_IMPORT_FAILED,
        DISCOVERY_FACTORY_RAISED,
        DISCOVERY_WRONG_TYPE,
        DISCOVERY_NAME_MISMATCH,
    }
    assert exercised == set(DISCOVERY_REFUSALS)


# ── every consumer x every refusal, asserted on the CODE ────────────────────


@pytest.mark.parametrize("key", sorted(CONSUMERS))
def test_zero_declarations_is_none_not_a_refusal(key: str) -> None:
    """A valid environment. The caller's own refusals then stand, and can say
    what was looked for."""
    assert CONSUMERS[key].discover(entries=[]) is None


@pytest.mark.parametrize("key", sorted(CONSUMERS))
def test_one_declaration_is_admitted(key: str) -> None:
    """THE POSITIVE CONTROL, per consumer. A discovery mechanism that has never
    admitted anything is the defect the refusals exist alongside, not instead
    of."""
    consumer = CONSUMERS[key]
    found = consumer.discover(entries=[_Entry(consumer.name, consumer.make)])
    assert found is not None


@pytest.mark.parametrize("key", sorted(CONSUMERS))
def test_two_declarations_refuse_naming_both(key: str) -> None:
    consumer = CONSUMERS[key]
    with pytest.raises(PreconditionFailed) as caught:
        consumer.discover(
            entries=[
                _Entry(consumer.name, consumer.make, dist="first-dist"),
                _Entry("rival", consumer.make, dist="second-dist"),
            ]
        )
    assert caught.value.code == DISCOVERY_AMBIGUOUS
    assert "first-dist" in str(caught.value)
    assert "second-dist" in str(caught.value)


@pytest.mark.parametrize("key", sorted(CONSUMERS))
def test_a_declaration_that_fails_to_import_refuses(key: str) -> None:
    consumer = CONSUMERS[key]
    with pytest.raises(PreconditionFailed) as caught:
        consumer.discover(
            entries=[
                _Entry(
                    consumer.name,
                    consumer.make,
                    load_error=ImportError("missing native dep"),
                )
            ]
        )
    assert caught.value.code == DISCOVERY_IMPORT_FAILED


@pytest.mark.parametrize("key", sorted(CONSUMERS))
def test_a_factory_that_raises_refuses(key: str) -> None:
    def broken() -> None:
        raise RuntimeError("keys unreadable")

    consumer = CONSUMERS[key]
    with pytest.raises(PreconditionFailed) as caught:
        consumer.discover(entries=[_Entry(consumer.name, broken)])
    assert caught.value.code == DISCOVERY_FACTORY_RAISED


@pytest.mark.parametrize("key", sorted(CONSUMERS))
def test_a_look_alike_result_refuses(key: str) -> None:
    class LookAlike:
        provider = "acme-host"
        application = "acme-host"

    consumer = CONSUMERS[key]
    with pytest.raises(PreconditionFailed) as caught:
        consumer.discover(entries=[_Entry(consumer.name, lambda: LookAlike())])
    assert caught.value.code == DISCOVERY_WRONG_TYPE


@pytest.mark.parametrize("key", sorted(CONSUMERS))
def test_a_declaration_answering_to_another_name_refuses(key: str) -> None:
    """Refusal 5, and the one an optional `name_of` would have let a consumer
    skip. The entry point name is what was visible before anything was
    imported."""
    consumer = CONSUMERS[key]
    with pytest.raises(PreconditionFailed) as caught:
        consumer.discover(entries=[_Entry(consumer.name, consumer.wrong_name)])
    assert caught.value.code == DISCOVERY_NAME_MISMATCH


# ── the core itself, driven directly ────────────────────────────────────────


class _Named:
    def __init__(self, name: str) -> None:
        self.name = name


def _core(entries: list[Any]):
    return discover_one(
        group="test.group",
        expected_type=_Named,
        subject="test subjects",
        name_of=lambda found: found.name,
        entries=entries,
    )


def test_the_core_admits_a_conforming_declaration() -> None:
    assert _core([_Entry("alpha", lambda: _Named("alpha"))]).name == "alpha"


def test_the_core_weaves_the_subject_into_its_refusals() -> None:
    """An operator reads a sentence about their deployment, not about a generic."""
    with pytest.raises(PreconditionFailed) as caught:
        _core(
            [
                _Entry("alpha", lambda: _Named("alpha")),
                _Entry("beta", lambda: _Named("beta")),
            ]
        )
    assert "test subjects" in str(caught.value)


def test_name_of_is_required_so_a_consumer_cannot_skip_refusal_five() -> None:
    """Structural, not a convention. An optional extractor would let a consumer
    acquire this core and never exercise the name check — the caller that never
    proves it."""
    import inspect

    parameter = inspect.signature(discovery.discover_one).parameters["name_of"]
    assert parameter.default is inspect.Parameter.empty
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY


def test_declared_names_reads_metadata_without_loading() -> None:
    """`validate` and a dry run must not import assembly code. An entry whose
    load() raises still enumerates, which proves no load happened."""
    exploding = _Entry("zeta", None, load_error=ImportError("must never be raised"))
    assert discovery.declared_names(
        "test.group", entries=[exploding, _Entry("alpha", None)]
    ) == ("alpha", "zeta")
