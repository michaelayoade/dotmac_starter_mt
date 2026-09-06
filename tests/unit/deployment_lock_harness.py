"""One real deployment-lock hold, for tests that drive a real `Executor`.

`Executor.run` and `Executor.rollback` require a `DeploymentLockHeld`, which
only `deployment_lock` can produce and which goes dead when its `with` block
exits. Every test that drives a real executor therefore needs a real hold — and
that is the boundary working rather than an inconvenience. Before it, those
tests mutated a modelled host with nothing serialising them, which is precisely
the shape the capability was added to make unwritable.

## Not a fake, and not a bypass

`deployment_lock` is entered for real, on a real file, taking a real `flock`;
the token handed back is the one it yielded. Nothing here touches the private
witness. The block is simply never exited, so the test PROCESS holds the
product's lock for its lifetime, the same way a deployment holds it for the
run's lifetime.

A test that wants an INVALID token still has to obtain one honestly and let it
die — `test_deployment_foundation_lock_capability` does exactly that, and its
refusal cases call `run` with no token at all.

## Why it lives here rather than beside the other executor fixtures

`test_deployment_foundation_execution_binding` was the obvious home, and it is
imported BY `test_deployment_foundation_failure_injection` — so putting it
there and importing it back would be a cycle. A helper used by both ends of an
existing import chain belongs below both of them.

## Per product, in a private directory

The two reasons `engine/lock.py` gives: mutual exclusion is a property of an
INODE rather than of a name, and two different products must not serialise. The
private temporary directory also keeps this process-wide hold from colliding
with tests that take the same product's lock under `tmp_path` — those get a
different inode and are unaffected.
"""

from __future__ import annotations

import tempfile

from dotmac_deployment_foundation.engine.lock import (
    DeploymentLockHeld,
    deployment_lock,
)

_HELD: dict[str, DeploymentLockHeld] = {}
#: The managers are kept referenced so their generators — and with them the
#: open descriptors the `flock`s live on — are not collected while a test is
#: still using the tokens they yielded.
_MANAGERS: list[object] = []


def held_lock(product: str) -> DeploymentLockHeld:
    """A genuine, live `DeploymentLockHeld` for ``product``."""
    if product not in _HELD:
        manager = deployment_lock(
            product,
            directory=tempfile.mkdtemp(prefix="dotmac-unit-lock-"),
            label=f"unit test process holding {product}",
        )
        _HELD[product] = manager.__enter__()
        _MANAGERS.append(manager)
    token = _HELD[product]
    assert token.live, (
        f"the process-wide hold on {product!r} is dead. Something exited the "
        "context manager; a token that outlives its hold is exactly what "
        "`DeploymentLockHeld.require_held` refuses"
    )
    return token
