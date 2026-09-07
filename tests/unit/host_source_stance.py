"""A GENUINE host-source stance, for tests that drive a real `Executor`.

`Executor.run`/`Executor.rollback` now call `require_host_source` themselves
(`engine/run.py::Executor._verify_host_source`) immediately after the caller's
lock is proven held and before everything else. Every EXISTING test that
builds a real `Executor` and drives `run`/`rollback` past that point therefore
needs to supply a host-source stance the SAME WAY it already supplies a lock
and a grant — through the constructor hooks `host_source_receipt` /
`host_source_metadata` — or the new mandatory prerequisite refuses before
reaching whatever the test is actually about.

`host_source_installed` and `host_source_probe` are GONE from `Executor`'s
constructor. They were the live bypass: `_verify_host_source` used to forward
both straight into `require_host_source` as `installed=`/`source_tree_digest=`,
so a caller stating the artifact digest or the launcher digest DIRECTLY was
admitted without either ever being read from this interpreter or this
launcher. Closing them means this stance no longer supplies either — there is
nothing left to supply. `host_source_metadata` alone is enough: `FakeInstall`
stands in for `importlib.metadata`, and `require_host_source` still parses its
real-shaped `RECORD`/`direct_url.json` content and computes the artifact
digest from that parse, exactly as it would from a real interpreter. The
launcher digest is never faked here at all — `_source_tree_digest` remains
importable for the low-level `require_host_source` unit tests in
`test_deployment_foundation_host_source.py`, which call that function
directly (a legitimate use: those tests are proving `require_host_source`
itself, not `Executor`), but nothing in this stance hands it to an `Executor`,
because `Executor` no longer accepts it.

## Why this is a STANCE and not a bypass

`valid_host_source_kwargs()` is built from the exact fixture shapes
`host_source.py`'s own test suite already uses for a genuinely ADMITTED
artifact: a `FakeInstall` shaped after a real `RECORD`/`direct_url.json`, and a
`CandidateReceipt` that agrees with it byte for byte. A test using it takes the
same successful path
`test_deployment_foundation_host_source.py::
test_a_genuine_artifact_matching_its_receipt_is_accepted` already proves, not
a special test-only admit rule, a weaker check, or a different code path.

Nothing here changes what `Executor` does when a caller omits this. The
standing negative control is
`test_deployment_foundation_host_source_gate.py::
test_a_bare_executor_refuses_by_default`: an `Executor` built with NONE of
these keywords still reads the REAL installed distribution and still refuses.
This module supplies ingredients a test can hand to the real gate; it holds no
reference to `require_host_source` itself and cannot patch, disable, or widen
it.

## Why a function, not a module-level constant

`CandidateReceipt` and the `Digest` values inside it are frozen and read-only
in practice, but returning fresh objects per call keeps one test's executor
from being able to be affected by another's — the identical reasoning
`test_deployment_foundation_execution_binding._fixture` already applies to
`RecordingEffects`.
"""

from __future__ import annotations

from typing import Any

from tests.unit.test_deployment_foundation_host_source import FakeInstall, _receipt


def valid_host_source_kwargs() -> dict[str, Any]:
    """Fresh `Executor(...)` keyword arguments, admitted by the real gate."""
    return {
        "host_source_receipt": _receipt(),
        "host_source_metadata": FakeInstall(),
    }
