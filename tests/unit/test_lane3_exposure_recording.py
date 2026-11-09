"""The Lane 3 recording adapter is transparent to the Executor.

The adapter exists to preserve the exact exposure boundaries the Executor
crosses. That purpose is defeated if it changes a result, translates an
exception, observes on its own initiative, or lets a call escape the record.
These tests therefore assert object and exception identity, not merely equal
values.
"""

from __future__ import annotations

import ast
import dataclasses
import pathlib
import sys
from collections.abc import Callable, Iterator, Sequence
from typing import Any

import pytest
from dotmac_deployment_foundation.exposure import (
    HostObservation,
    ObservedChain,
    ObservedRule,
)
from dotmac_deployment_foundation.ingress import FirewallRule

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import lane3_exposure_recording as recording  # noqa: E402

ExposureCallRecord = recording.ExposureCallRecord
RecordingExposureEffects = recording.RecordingExposureEffects


class _Clock:
    def __init__(self, *values: float) -> None:
        self._values = iter(values)

    def __call__(self) -> float:
        return next(self._values)


class _Inner:
    def __init__(
        self,
        *observations: HostObservation,
        failures: dict[str, BaseException] | None = None,
    ) -> None:
        self._observations: Iterator[HostObservation] = iter(observations)
        self._failures = failures or {}
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.descriptor_reads = 0

    @property
    def execution_descriptor(self) -> bytes:
        self.descriptor_reads += 1
        return b"descriptor-bytes"

    def _maybe_fail(self, method: str) -> None:
        failure = self._failures.get(method)
        if failure is not None:
            raise failure

    def observe(self) -> HostObservation:
        self.calls.append(("observe", (), {}))
        self._maybe_fail("observe")
        return next(self._observations)

    def apply_compose(self, command: Sequence[str], *, timeout_seconds: int) -> None:
        self.calls.append(
            ("apply_compose", (command,), {"timeout_seconds": timeout_seconds})
        )
        self._maybe_fail("apply_compose")

    def replace_rules(
        self, family: str, chain: str, rules: Sequence[FirewallRule]
    ) -> None:
        self.calls.append(("replace_rules", (family, chain, rules), {}))
        self._maybe_fail("replace_rules")

    def restore_chains(self, chains: Sequence[ObservedChain]) -> None:
        self.calls.append(("restore_chains", (chains,), {}))
        self._maybe_fail("restore_chains")


def _observation(port: int) -> HostObservation:
    return HostObservation(
        chains=(
            ObservedChain(
                family="ipv4",
                name="DOCKER-USER",
                policy="-",
                rules=(
                    ObservedRule(
                        family="ipv4",
                        chain="DOCKER-USER",
                        arguments=f"-p tcp --ctorigdstport {port} -j ACCEPT",
                    ),
                ),
            ),
        )
    )


def _rule() -> FirewallRule:
    return FirewallRule(
        family="ipv4",
        chain="DOCKER-USER",
        protocol="tcp",
        host_port=19001,
        action="ACCEPT",
        source_set="office",
        terminal=False,
    )


def test_construction_and_descriptor_access_do_not_observe_or_record() -> None:
    sink: list[ExposureCallRecord] = []
    inner = _Inner(_observation(19001))
    adapter = RecordingExposureEffects(inner, sink, clock=_Clock())

    assert inner.calls == []
    assert inner.descriptor_reads == 0
    assert adapter.execution_descriptor == b"descriptor-bytes"
    assert adapter.execution_descriptor == b"descriptor-bytes"
    assert inner.descriptor_reads == 2
    assert inner.calls == []
    assert sink == []


def test_observe_returns_the_exact_inner_object_and_records_it() -> None:
    sink: list[ExposureCallRecord] = []
    observed = _observation(19001)
    adapter = RecordingExposureEffects(
        _Inner(observed), sink, clock=_Clock(10.0, 10.25)
    )

    assert adapter.observe() is observed
    assert sink == [
        ExposureCallRecord(
            sequence=0,
            method="observe",
            observation=observed,
            duration_seconds=0.25,
        )
    ]


def test_mutating_calls_forward_original_arguments_and_record_snapshots() -> None:
    sink: list[ExposureCallRecord] = []
    inner = _Inner()
    adapter = RecordingExposureEffects(
        inner, sink, clock=_Clock(1.0, 2.0, 3.0, 6.0, 8.0, 8.5)
    )
    command = ["up", "-d"]
    rule = _rule()
    rules = [rule]
    chain = _observation(19001).chains[0]
    chains = [chain]

    assert adapter.apply_compose(command, timeout_seconds=30) is None
    assert adapter.replace_rules("ipv4", "DOCKER-USER", rules) is None
    assert adapter.restore_chains(chains) is None

    # The inner effects receive the caller's exact sequence objects.
    assert inner.calls[0][1][0] is command
    assert inner.calls[1][1][2] is rules
    assert inner.calls[2][1][0] is chains

    # Later caller mutation cannot rewrite the recorded boundary.
    command.append("later")
    rules.clear()
    chains.clear()
    assert sink == [
        ExposureCallRecord(
            sequence=0,
            method="apply_compose",
            command=("up", "-d"),
            timeout_seconds=30,
            duration_seconds=1.0,
        ),
        ExposureCallRecord(
            sequence=1,
            method="replace_rules",
            family="ipv4",
            chain="DOCKER-USER",
            rules=(rule,),
            duration_seconds=3.0,
        ),
        ExposureCallRecord(
            sequence=2,
            method="restore_chains",
            chains=(chain,),
            duration_seconds=0.5,
        ),
    ]


class _FailingSink(list[ExposureCallRecord]):
    def append(self, item: ExposureCallRecord) -> None:
        raise RuntimeError("sink unavailable")


@pytest.mark.parametrize(
    ("method", "call"),
    [
        ("observe", lambda adapter: adapter.observe()),
        (
            "apply_compose",
            lambda adapter: adapter.apply_compose(["up"], timeout_seconds=1),
        ),
        (
            "replace_rules",
            lambda adapter: adapter.replace_rules("ipv4", "DOCKER-USER", (_rule(),)),
        ),
        (
            "restore_chains",
            lambda adapter: adapter.restore_chains(_observation(19001).chains),
        ),
    ],
)
def test_exceptions_keep_identity_and_are_recorded(
    method: str, call: Callable[[RecordingExposureEffects], Any]
) -> None:
    sink: list[ExposureCallRecord] = []
    failure = RuntimeError(f"{method} failed")
    adapter = RecordingExposureEffects(
        _Inner(failures={method: failure}), sink, clock=_Clock(4.0, 4.75)
    )

    with pytest.raises(RuntimeError) as caught:
        call(adapter)

    assert caught.value is failure
    assert len(sink) == 1
    assert sink[0].sequence == 0
    assert sink[0].method == method
    assert sink[0].exception_type == "builtins.RuntimeError"
    assert sink[0].duration_seconds == 0.75
    if method == "apply_compose":
        assert sink[0].command == ("up",)
        assert sink[0].timeout_seconds == 1
    elif method == "replace_rules":
        assert sink[0].family == "ipv4"
        assert sink[0].chain == "DOCKER-USER"
        assert sink[0].rules == (_rule(),)
    elif method == "restore_chains":
        assert sink[0].chains == _observation(19001).chains


def test_recording_failure_never_changes_the_delegated_outcome() -> None:
    observed = _observation(19001)
    adapter = RecordingExposureEffects(
        _Inner(observed), _FailingSink(), clock=_Clock(1.0, 1.5)
    )
    assert adapter.observe() is observed

    failure = RuntimeError("inner failed")
    failing = RecordingExposureEffects(
        _Inner(failures={"observe": failure}),
        _FailingSink(),
        clock=_Clock(2.0, 2.5),
    )
    with pytest.raises(RuntimeError) as caught:
        failing.observe()
    assert caught.value is failure


def test_base_exceptions_are_recorded_and_reraised_unchanged() -> None:
    sink: list[ExposureCallRecord] = []
    failure = KeyboardInterrupt()
    adapter = RecordingExposureEffects(
        _Inner(failures={"observe": failure}), sink, clock=_Clock(1.0, 2.0)
    )

    with pytest.raises(KeyboardInterrupt) as caught:
        adapter.observe()

    assert caught.value is failure
    assert sink[0].exception_type == "builtins.KeyboardInterrupt"


def test_records_are_immutable() -> None:
    sink: list[ExposureCallRecord] = []
    adapter = RecordingExposureEffects(
        _Inner(_observation(19001)), sink, clock=_Clock(1.0, 1.0)
    )
    adapter.observe()

    with pytest.raises(dataclasses.FrozenInstanceError):
        sink[0].method = "rewritten"  # type: ignore[misc]


def _adapter_source() -> ast.Module:
    return ast.parse(
        (REPO / "scripts" / "lane3_exposure_recording.py").read_text("utf-8")
    )


def test_each_protocol_method_delegates_once_to_the_same_inner_method() -> None:
    adapter = next(
        node
        for node in _adapter_source().body
        if isinstance(node, ast.ClassDef) and node.name == "RecordingExposureEffects"
    )
    methods = {
        node.name: node for node in adapter.body if isinstance(node, ast.FunctionDef)
    }
    for name in ("observe", "apply_compose", "replace_rules", "restore_chains"):
        delegated = [
            ast.unparse(node.func)
            for node in ast.walk(methods[name])
            if isinstance(node, ast.Call)
            and ast.unparse(node.func).startswith("self._inner.")
        ]
        assert delegated == [f"self._inner.{name}"]

    mutators = {"apply_compose", "replace_rules", "restore_chains"}
    every_mutator_call = sorted(
        ast.unparse(node.func)
        for node in ast.walk(_adapter_source())
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in mutators
    )
    assert every_mutator_call == [
        "self._inner.apply_compose",
        "self._inner.replace_rules",
        "self._inner.restore_chains",
    ]


def test_the_adapter_module_has_no_executor_or_host_seam_of_its_own() -> None:
    tree = _adapter_source()
    assert [node.name for node in tree.body if isinstance(node, ast.ClassDef)] == [
        "ExposureCallRecord",
        "RecordingExposureEffects",
    ]
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert imported == {
        "__future__",
        "time",
        "collections.abc",
        "dataclasses",
        "typing",
        "dotmac_deployment_foundation.exposure",
        "dotmac_deployment_foundation.ingress",
    }
    called_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "getattr" not in called_names
