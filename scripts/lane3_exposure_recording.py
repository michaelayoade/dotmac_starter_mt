"""Transparent recording around Lane 3's Executor exposure seam.

The runner already photographs the host before handing control to the
Executor, but that is not necessarily the observation from which the Executor
restores: Compose convergence runs between the two reads. This adapter wraps
only the object handed to the Executor, forwards every call and result
unchanged, and appends the exact boundaries crossed to a runner-owned sink.
It makes no deployment decision and orders no operation of its own: each
method delegates once to the same-named inner method, records what crossed,
and returns or re-raises exactly what the inner effects produced.

Recording is best-effort evidence only: a failure to append a record never
changes the delegated call's result or exception, because an evidence wrapper
must not become a second failure mode for a host mutation or its compensation.
A missing record therefore proves nothing and must fail any later proof join
as incomplete evidence.

The raw records are private operational evidence. They are deliberately held
only in memory here; a later Lane 3 proof pipeline owns joining and private
serialization.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from dotmac_deployment_foundation.exposure import (
    ExposureEffects,
    HostObservation,
    ObservedChain,
)
from dotmac_deployment_foundation.ingress import FirewallRule

ExposureMethod = Literal["observe", "apply_compose", "replace_rules", "restore_chains"]


@dataclass(frozen=True, slots=True)
class ExposureCallRecord:
    """One completed or failed call across the Executor exposure boundary."""

    sequence: int
    method: ExposureMethod
    family: str = ""
    chain: str = ""
    rules: tuple[FirewallRule, ...] = ()
    chains: tuple[ObservedChain, ...] = ()
    command: tuple[str, ...] = ()
    timeout_seconds: int | None = None
    observation: HostObservation | None = None
    exception_type: str = ""
    duration_seconds: float = 0.0


class RecordingExposureEffects:
    """Forward `ExposureEffects` calls unchanged while recording each one."""

    def __init__(
        self,
        inner: ExposureEffects,
        sink: list[ExposureCallRecord],
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._inner = inner
        self._sink = sink
        self._clock = clock

    @property
    def execution_descriptor(self) -> bytes:
        return self._inner.execution_descriptor

    def _record(
        self,
        method: ExposureMethod,
        *,
        started: float,
        family: str = "",
        chain: str = "",
        rules: tuple[FirewallRule, ...] = (),
        chains: tuple[ObservedChain, ...] = (),
        command: tuple[str, ...] = (),
        timeout_seconds: int | None = None,
        observation: HostObservation | None = None,
        exception_type: str = "",
    ) -> None:
        try:
            self._sink.append(
                ExposureCallRecord(
                    sequence=len(self._sink),
                    method=method,
                    family=family,
                    chain=chain,
                    rules=rules,
                    chains=chains,
                    command=command,
                    timeout_seconds=timeout_seconds,
                    observation=observation,
                    exception_type=exception_type,
                    duration_seconds=self._clock() - started,
                )
            )
        except Exception:
            # Evidence loss is preferable to rewriting a host mutation's
            # outcome. `BaseException` still propagates: an interrupt must not
            # be mistaken for an ordinary recording failure.
            return

    @staticmethod
    def _exception_type(exc: BaseException) -> str:
        exc_type = type(exc)
        return f"{exc_type.__module__}.{exc_type.__qualname__}"

    def observe(self) -> HostObservation:
        started = self._clock()
        try:
            observation = self._inner.observe()
        except BaseException as exc:
            self._record(
                "observe",
                started=started,
                exception_type=self._exception_type(exc),
            )
            raise
        self._record("observe", started=started, observation=observation)
        return observation

    def apply_compose(self, command: Sequence[str], *, timeout_seconds: int) -> None:
        recorded_command = tuple(command)
        started = self._clock()
        try:
            result = self._inner.apply_compose(command, timeout_seconds=timeout_seconds)
        except BaseException as exc:
            self._record(
                "apply_compose",
                started=started,
                command=recorded_command,
                timeout_seconds=timeout_seconds,
                exception_type=self._exception_type(exc),
            )
            raise
        self._record(
            "apply_compose",
            started=started,
            command=recorded_command,
            timeout_seconds=timeout_seconds,
        )
        return result

    def replace_rules(
        self, family: str, chain: str, rules: Sequence[FirewallRule]
    ) -> None:
        recorded_rules = tuple(rules)
        started = self._clock()
        try:
            result = self._inner.replace_rules(family, chain, rules)
        except BaseException as exc:
            self._record(
                "replace_rules",
                started=started,
                family=family,
                chain=chain,
                rules=recorded_rules,
                exception_type=self._exception_type(exc),
            )
            raise
        self._record(
            "replace_rules",
            started=started,
            family=family,
            chain=chain,
            rules=recorded_rules,
        )
        return result

    def restore_chains(self, chains: Sequence[ObservedChain]) -> None:
        recorded_chains = tuple(chains)
        started = self._clock()
        try:
            result = self._inner.restore_chains(chains)
        except BaseException as exc:
            self._record(
                "restore_chains",
                started=started,
                chains=recorded_chains,
                exception_type=self._exception_type(exc),
            )
            raise
        self._record("restore_chains", started=started, chains=recorded_chains)
        return result
