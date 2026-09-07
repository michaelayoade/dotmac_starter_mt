"""There is no genuine host-source stance a test can construct any more.

## Why this module used to exist, and why the thing it built was the defect

`Executor.run`/`Executor.rollback` call `require_host_source` themselves
(`engine/run.py::Executor._verify_host_source`) immediately after the
caller's lock is proven held and before everything else. This module used to
supply `valid_host_source_kwargs()` — a `host_source_receipt=` (a
`CandidateReceipt`, a plain frozen dataclass carrying `artifact_digest`
directly) paired with a `host_source_metadata=` (`FakeInstall`, whose
`read_text("direct_url.json")` returns a caller-chosen JSON string) built to
agree with each other byte for byte.

An independent review at `541cee5d` named this correctly:
`valid_host_source_kwargs()` **was the exploit** `host_source_installed=`
originally produced, respelled with a `json.dumps` in between. Supplying a
`CandidateReceipt` and an `InstalledMetadata` that agree is not "the same
successful path a genuine artifact takes" — it is a caller stating the same
digest on both sides of the one comparison `require_host_source` makes.
`CandidateReceipt` and `InstalledMetadata` are PARSING interfaces (they turn
a document/a `.dist-info` reading into a typed value); neither is
authority-bearing evidence, and no combination of the two — however
carefully the fixture agrees with itself — turns caller-authored data into
proof that a trusted third party attested to it.

Michael's ruling: `Executor`/`RecoveryExecutor` now accept **no host-source
parameter of any kind**. `_verify_host_source` always calls
`require_host_source(receipt=None)`, which always refuses — a typed refusal
(`NO_RECEIPT`/`ABSENT`/`WRONG_KIND`) with zero effects, in every environment,
including this one. There is no way to construct an "admitted" `Executor` or
`RecoveryExecutor` any longer, by design, until trusted provenance (an
externally committed/signed candidate attestation, an independently signed
installed-host observation, checked against distinct trust roots) lands as
its own separate piece of work.

## What this means for every test that used to call this module

Every test that built an `Executor`/`RecoveryExecutor` via
`valid_host_source_kwargs()` in order to reach PAST the host-source gate and
exercise something downstream of it (lock capability, the authorization
cross-matrix, failure injection, principal bootstrap, deployment evidence,
external recovery) can no longer reach that subject through this seam at
all — `run()`/`rollback()` refuse before any of it. That is not a test gap to
be engineered around here: Michael was explicit that losing this coverage
and naming it is the correct outcome, not a defect to route around with a
new test-only hook (which is exactly how the prior "repair" reintroduced the
bypass).

`valid_host_source_kwargs()` therefore no longer returns admitting kwargs —
there are none to return. It calls `pytest.skip` with this explanation, so
every test still calling it (the call sites have not all been individually
rewritten — see each file's own note) is marked SKIPPED rather than
FAILING for an unrelated reason or silently reporting a false pass. A
skipped test is visible lost coverage; a test that raises `TypeError` on an
unexpected keyword is a maintenance fire that obscures the real finding.

The behaviours those tests exercised — the pieces of `Executor`/
`RecoveryExecutor` beyond the host-source gate — are UNMONITORED by this
suite until trusted provenance lands and a genuine admission path exists to
drive them through again. `test_deployment_foundation_host_source_gate.py`
and `test_deployment_foundation_recovery_execution.py` are NOT quarantined
this way — those files are host_source's OWN test suites and have been
rewritten directly to assert the new posture (unconditional refusal), not
routed through this stub.
"""

from __future__ import annotations

from typing import Any

import pytest


def valid_host_source_kwargs() -> dict[str, Any]:
    """No longer returns anything admitting — see the module docstring.

    Skips the calling test rather than returning a value, so every remaining
    call site (unrewritten test bodies that still call this) is reported as
    lost coverage rather than as a spurious pass or an unrelated crash.
    """
    pytest.skip(
        "unreachable since the host-source gate accepts no caller-suppliable "
        "receipt or metadata at all (Executor/RecoveryExecutor always refuse "
        "require_host_source(receipt=None)); this test's subject is beyond "
        "the gate and is unmonitored until trusted provenance lands — see "
        "host_source_stance.py"
    )
    raise AssertionError("unreachable")  # pragma: no cover
