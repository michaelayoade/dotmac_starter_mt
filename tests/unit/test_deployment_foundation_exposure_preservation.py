"""The preservation property, held by the TRANSACTION rather than a provider.

`ComposeHostExposureEffects` is careful never to flush a shared chain, and
`test_deployment_foundation_exposure_host.py` proves it. That is a property of
one implementation. This file covers the guarantee one level up: whatever
implements `ExposureEffects`, :class:`ExposureTransaction` measures the rules it
does not own before and after, and refuses when one of them has vanished.

Why that split is worth a second file. The rule Michael set is *"the controller
must never restore an entire shared firewall chain"* — and the obvious
implementation of `restore_chains` is snapshot, flush, replay, which deletes
whatever another process legitimately added while the transaction ran. A
provider that regresses to that shape would keep passing its own tests if the
only guard lived beside it, because those tests are written by whoever wrote
the provider. Here the fake is deliberately hostile.

**The asymmetry is deliberate and is asserted.** A foreign rule that VANISHED
was deleted by us — the data-loss bug wearing the word *restore*. A foreign rule
that APPEARED was written by somebody else while we held the lock, and refusing
on it would reject the correct behaviour of preserving a rule that arrived
mid-transaction. Only the first is a refusal.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest
from dotmac_deployment_foundation.errors import PreconditionFailed
from dotmac_deployment_foundation.exposure import (
    ExposureTransaction,
    HostObservation,
    ObservedChain,
    ObservedRule,
    foreign_rules,
    ownership_comment,
)
from dotmac_deployment_foundation.spec import ProductDeploymentSpec

_MANIFEST_DIGEST = "sha256:" + "a" * 64
_IMAGE = f"registry.example.com/acme/app@sha256:{'b' * 64}"

_DESCRIPTOR = f"""
schema = "ProductDeploymentSpec.v1"
product = "acme"
environment = "prod"

[assembly]
manifest_path = "deploy/product.toml"
manifest_digest = "{_MANIFEST_DIGEST}"

[image]
reference = "{_IMAGE}"
source_revision = "{"c" * 40}"

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
replicas = 1
materials = ["DATABASE_URL"]

[roles.resources]
cpus = "0.5"
memory = "256m"

[roles.health.ready]
path = "/readyz"
port = 8003

[[roles.ports]]
container = 5432
host = 9001
protocol = "tcp"
exposure = "private"
address_family = "dual_stack"
tls = "mtls"
authentication = "mtls"
source_set = "acme-clients"
telemetry = true
"""

OWNER = ownership_comment("acme")

#: A rule belonging to something else entirely — a different port, no ownership
#: comment. This is the preservation canary.
FOREIGN = ObservedRule(
    family="ipv4",
    chain="DOCKER-USER",
    arguments="-p tcp --ctorigdstport 7777 -s 10.9.0.0/24 -j ACCEPT",
)

#: One of ours, labelled. Removing this during a rollback is correct.
OURS = ObservedRule(
    family="ipv4",
    chain="DOCKER-USER",
    arguments=f"-p tcp --ctorigdstport 9001 -m comment --comment {OWNER} -j ACCEPT",
)

#: Unlabelled, but on a port WE publish. Conservatively treated as ours rather
#: than as unrelated host state — see `foreign_rules`.
UNLABELLED_ON_OUR_PORT = ObservedRule(
    family="ipv4",
    chain="DOCKER-USER",
    arguments="-p tcp --ctorigdstport 9001 -j DROP",
)


@pytest.fixture(scope="module")
def spec() -> ProductDeploymentSpec:
    return ProductDeploymentSpec.loads(_DESCRIPTOR, source="<preservation-fixture>")


def _observation(*rules: ObservedRule) -> HostObservation:
    return HostObservation(
        chains=(
            ObservedChain(
                family="ipv4", name="DOCKER-USER", policy="-", rules=tuple(rules)
            ),
        )
    )


# ── what counts as somebody else's ──────────────────────────────────────────


def test_a_labelled_rule_is_ours() -> None:
    assert foreign_rules(_observation(OURS), owner=OWNER) == ()


def test_an_unrelated_rule_is_foreign() -> None:
    assert foreign_rules(_observation(FOREIGN), owner=OWNER) == (FOREIGN,)


def test_an_unlabelled_rule_on_a_port_we_publish_is_not_treated_as_foreign() -> None:
    """Either a predecessor of ours or a conflict on our own port. Calling it
    "unrelated host state" would be wrong in both readings, and would make a
    correct rollback of our own leftover look like data loss."""
    observation = _observation(UNLABELLED_ON_OUR_PORT)
    assert foreign_rules(observation, owner=OWNER, managed_ports=(9001,)) == ()
    # Sensitivity: without the managed-port exclusion it IS reported, so the
    # exclusion is doing work rather than the rule being invisible anyway.
    assert foreign_rules(observation, owner=OWNER) == (UNLABELLED_ON_OUR_PORT,)


def test_only_the_shared_filter_chains_are_examined() -> None:
    """`DOCKER-USER` on IPv4 and `INPUT` on IPv6 are the two chains this
    facility writes into. A rule in some other chain was never ours to preserve
    or to delete, and counting it would make every run look contaminated."""
    elsewhere = HostObservation(
        chains=(
            ObservedChain(
                family="ipv4",
                name="FORWARD",
                policy="-",
                rules=(
                    ObservedRule(
                        family="ipv4", chain="FORWARD", arguments="-j SOMETHING"
                    ),
                ),
            ),
        )
    )
    assert foreign_rules(elsewhere, owner=OWNER) == ()


# ── the transaction refuses a rollback that took somebody else's rule ───────


class _Effects:
    """A host whose rollback behaviour the test chooses.

    `restore_chains` is where the damage happens in the real failure, so that
    is the knob: `destructive=True` models the snapshot-flush-replay shape by
    dropping every rule that is not in the snapshot's own list — which is
    exactly what replaying a captured chain does to a rule added since.
    """

    def __init__(
        self, *, before: HostObservation, after: HostObservation, destructive: bool
    ) -> None:
        self._before = before
        self._after = after
        self._destructive = destructive
        self._restored = False
        self.calls: list[str] = []

    def observe(self) -> HostObservation:
        self.calls.append("observe")
        if self._restored:
            return self._before if not self._destructive else _observation()
        return self._after if self.calls.count("observe") > 1 else self._before

    def apply_compose(self, command: Sequence[str], *, timeout_seconds: int) -> None:
        self.calls.append("apply")

    def replace_rules(self, family: str, chain: str, rules: Sequence[object]) -> None:
        self.calls.append("replace")

    def restore_chains(self, chains: Sequence[ObservedChain]) -> None:
        self.calls.append("restore")
        self._restored = True


def test_a_rollback_that_replays_a_whole_chain_is_refused(
    spec: ProductDeploymentSpec, tmp_path: Path
) -> None:
    """The central property. Verification fails, so a rollback runs; the
    destructive provider wipes the shared chain on the way back, and the
    transaction catches it by LOOKING rather than by trusting."""
    effects = _Effects(
        before=_observation(FOREIGN),
        after=_observation(FOREIGN),
        destructive=True,
    )
    transaction = ExposureTransaction(
        spec=spec, effects=effects, lock_directory=tmp_path
    )
    with pytest.raises(PreconditionFailed) as excinfo:
        transaction.run()
    message = str(excinfo.value)
    assert "does not own" in message
    assert "never restored wholesale" in message
    assert "restore" in effects.calls


def test_a_preserving_rollback_reports_the_verification_failure_instead(
    spec: ProductDeploymentSpec, tmp_path: Path
) -> None:
    """The negative control. Same failing verification, same rollback — but the
    provider keeps the foreign rule, so the error the caller sees is the real
    one about the exposure rather than a preservation refusal.

    Without this, the test above would pass against a transaction that refused
    every rollback for any reason at all.
    """
    effects = _Effects(
        before=_observation(FOREIGN),
        after=_observation(FOREIGN),
        destructive=False,
    )
    transaction = ExposureTransaction(
        spec=spec, effects=effects, lock_directory=tmp_path
    )
    with pytest.raises(PreconditionFailed) as excinfo:
        transaction.run()
    message = str(excinfo.value)
    assert "did not verify and was rolled back" in message
    assert "does not own" not in message
    assert transaction.rolled_back is True


def test_a_foreign_rule_that_appears_mid_transaction_is_not_a_refusal(
    spec: ProductDeploymentSpec, tmp_path: Path
) -> None:
    """Somebody else wrote to a shared chain while we held the lock. That is
    noise about the host's exclusivity, not evidence that we replaced
    anything — and refusing on it would reject the correct behaviour of
    preserving a rule that arrived after the snapshot."""
    arrived = ObservedRule(
        family="ipv4",
        chain="DOCKER-USER",
        arguments="-p tcp --ctorigdstport 8888 -j ACCEPT",
    )
    effects = _Effects(
        before=_observation(FOREIGN),
        after=_observation(FOREIGN, arrived),
        destructive=False,
    )
    transaction = ExposureTransaction(
        spec=spec, effects=effects, lock_directory=tmp_path
    )
    with pytest.raises(PreconditionFailed) as excinfo:
        transaction.run()
    # It still fails — the exposure does not verify — but NOT for preservation.
    assert "does not own" not in str(excinfo.value)
