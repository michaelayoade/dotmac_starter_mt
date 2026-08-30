"""`ComposeHostExposureEffects` — and the preservation property, proven.

Items 1-3 and 8 of the exposure rehearsal were hand-driven, which proves an
operator can apply and roll back, not that the code can. This file drives the
real provider through `ExposureTransaction` with a scripted runner.

## The property under test

**A rollback restores what THIS transaction changed and nothing else.**

The obvious implementation — snapshot the chain, flush it, replay the snapshot
— deletes any rule another process added while the transaction ran. Both chains
this facility writes into (`DOCKER-USER` on IPv4, `INPUT` on IPv6) are shared
with everything else on the host, so on a host carrying other work that is a
data-loss bug wearing the word *restore*.

Every proof here therefore plants a FOREIGN rule and asserts it survives —
including the case where it appears MID-TRANSACTION, which is the one a
snapshot-replay implementation gets wrong and a diff-based one cannot tell from
a bookkeeping failure.
"""

from __future__ import annotations

import shlex
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest
from dotmac_deployment_foundation.engine.run import CommandResult
from dotmac_deployment_foundation.errors import PreconditionFailed, StepFailed
from dotmac_deployment_foundation.exposure import (
    APPLY_COMMAND,
    ExposureTransaction,
)
from dotmac_deployment_foundation.providers.exposure_host import (
    ComposeHostExposureEffects,
    ownership_comment,
)
from dotmac_deployment_foundation.spec import ProductDeploymentSpec

_IMAGE = f"registry.example.com/acme/app@sha256:{'b' * 64}"

_DESCRIPTOR = f"""
schema = "ProductDeploymentSpec.v1"
product = "acme"
environment = "prod"

[assembly]
manifest_path = "deploy/product.toml"
manifest_digest = "sha256:{'a' * 64}"

[image]
reference = "{_IMAGE}"
source_revision = "{'c' * 40}"

[migration]
command = ["alembic", "upgrade", "heads"]
heads_command = ["alembic", "current"]
owner_material = "MIGRATION_DATABASE_URL"
expected_heads = ["abc123"]
compatibility = "online"
lock_timeout_seconds = 300

[[roles]]
code = "db"
command = ["postgres"]
replicas = 1

[roles.resources]
cpus = "0.5"
memory = "256m"

[roles.health.ready]
path = "/readyz"
port = 5432

[[roles.ports]]
container = 5432
host = 9001
protocol = "tcp"
exposure = "private"
address_family = "ipv4"
tls = "none"
source_set = "operations-vpn"
"""

OWNER = ownership_comment("acme")

#: A rule this transaction did not create. Every proof asserts it survives.
FOREIGN = (
    "-A DOCKER-USER -p tcp -m conntrack --ctorigdstport 55432 "
    '-m comment --comment "someone-elses-work" -j ACCEPT'
)

CONFORMING_SS = (
    "LISTEN 0 4096 10.20.0.7:9001 0.0.0.0:* " 'users:(("docker-proxy",pid=101,fd=7))'
)
CONFORMING_PS = (
    "root 101 /usr/bin/docker-proxy -proto tcp -host-ip 10.20.0.7 "
    "-host-port 9001 -container-ip 172.18.0.4 -container-port 5432"
)


def _v4_dump(*rules: str) -> str:
    body = "\n".join(rules)
    return f"*filter\n:DOCKER-USER - [0:0]\n{body}\nCOMMIT\n"


def _v6_dump(*rules: str) -> str:
    body = "\n".join(rules)
    return f"*filter\n:INPUT ACCEPT [0:0]\n:DOCKER-USER - [0:0]\n{body}\nCOMMIT\n"


@pytest.fixture()
def spec() -> ProductDeploymentSpec:
    return ProductDeploymentSpec.loads(_DESCRIPTOR, source="<exposure-host>")


class FakeHost:
    """A host whose firewall chains are real lists this test can inspect.

    Modelled as state rather than as a script of canned outputs, because the
    property under test is about what SURVIVES a sequence of operations. A
    canned-output fake would let a wrong implementation pass by never being
    asked the question that exposes it.
    """

    def __init__(self, *, v4: list[str] | None = None, v6: list[str] | None = None):
        self.chains: dict[str, list[str]] = {
            "ipv4": list(v4 or []),
            "ipv6": list(v6 or []),
        }
        self.calls: list[list[str]] = []
        self.sockets = CONFORMING_SS
        self.processes = CONFORMING_PS
        self.compose_failure = False
        #: Injected between the apply and the re-observation, so a rule can
        #: appear exactly where a snapshot-replay implementation loses it.
        self.on_apply = None

    def _family(self, binary: str) -> str:
        return "ipv6" if binary.startswith("ip6") else "ipv4"

    def __call__(
        self,
        argv: Sequence[str],
        *,
        timeout: int,
        env: Mapping[str, str] | None = None,
        capture: bool = True,
    ) -> CommandResult:
        argv = list(argv)
        self.calls.append(argv)
        binary = Path(argv[0]).name

        if binary.endswith("-save"):
            family = self._family(binary)
            dump = _v4_dump if family == "ipv4" else _v6_dump
            return CommandResult(0, dump(*self.chains[family]))
        if binary == "ss":
            return CommandResult(0, self.sockets)
        if binary == "ps":
            return CommandResult(0, self.processes)
        if binary == "docker":
            if self.compose_failure:
                return CommandResult(1, "", "compose refused")
            if self.on_apply is not None:
                self.on_apply(self)
            return CommandResult(0, "recreated")
        if binary in ("iptables", "ip6tables"):
            return self._firewall(binary, argv)
        return CommandResult(0, "")

    def _firewall(self, binary: str, argv: list[str]) -> CommandResult:
        family = self._family(binary)
        verb, chain = argv[1], argv[2]
        line = f"-A {chain} " + shlex.join(argv[3:])
        if verb == "-A":
            self.chains[family].append(line)
            return CommandResult(0, "")
        if verb == "-D":
            target = f"-A {chain} " + shlex.join(argv[3:])
            for existing in list(self.chains[family]):
                if _same_rule(existing, target):
                    self.chains[family].remove(existing)
                    return CommandResult(0, "")
            return CommandResult(1, "", "No chain/target/match by that name")
        return CommandResult(0, "")

    def rules(self, family: str) -> list[str]:
        return list(self.chains[family])


def _same_rule(left: str, right: str) -> bool:
    """Compare rules by token set, so quoting differences do not matter.

    `iptables-save` emits `--comment "x"` while an argv carries `--comment x`.
    A string compare would make every delete miss and the tests would pass by
    deleting nothing, which is the opposite of what they assert.
    """
    return shlex.split(left.replace('"', "")) == shlex.split(right.replace('"', ""))


def _effects(host: FakeHost, spec: ProductDeploymentSpec):
    return ComposeHostExposureEffects(spec, deploy_dir="/opt/acme", runner=host)


# ── the provider on its own ─────────────────────────────────────────────────


def test_observe_reads_sockets_processes_and_both_families(
    spec: ProductDeploymentSpec,
) -> None:
    host = FakeHost()
    observed = _effects(host, spec).observe()
    assert [socket.port for socket in observed.sockets] == [9001]
    assert [proxy.container_port for proxy in observed.proxies] == [5432]
    assert {chain.family for chain in observed.chains} == {"ipv4", "ipv6"}


def test_observe_does_not_invent_a_closed_port_behaviour(
    spec: ProductDeploymentSpec,
) -> None:
    """How a host answers a stranger cannot be determined from inside it.

    An invented value here would make `conclude_binding` treat a probe as
    conclusive when it is not — the failure mode the whole vantage discipline
    exists to prevent, reintroduced from the other end.
    """
    assert _effects(FakeHost(), spec).observe().closed_port_behaviour == "unknown"


def test_every_inserted_rule_carries_the_ownership_comment(
    spec: ProductDeploymentSpec,
) -> None:
    """Ownership lives in the rule, not in a diff against a snapshot.

    A diff cannot distinguish "someone else added this" from "we failed to
    record adding this", and those two need opposite handling.
    """
    from dotmac_deployment_foundation.policy import build_firewall_plan

    host = FakeHost()
    rules = [r for r in build_firewall_plan(spec) if r.family == "ipv4"]
    _effects(host, spec).replace_rules("ipv4", "DOCKER-USER", rules)
    assert host.rules("ipv4")
    assert all(OWNER in line for line in host.rules("ipv4"))


def test_the_inserted_rule_matches_what_the_PLAN_renders(
    spec: ProductDeploymentSpec,
) -> None:
    """The provider must not re-implement the port match.

    9001 -> 5432 is remapped, so `DOCKER-USER` needs `--ctorigdstport`. A
    `--dport` here would filter nothing while reading, in a diff, exactly like
    a rule that works — which is how it happened on this fleet.
    """
    from dotmac_deployment_foundation.policy import build_firewall_plan

    host = FakeHost()
    rules = [r for r in build_firewall_plan(spec) if r.family == "ipv4"]
    _effects(host, spec).replace_rules("ipv4", "DOCKER-USER", rules)
    assert all("--ctorigdstport 9001" in line for line in host.rules("ipv4"))
    assert not any("--dport" in line for line in host.rules("ipv4"))
    assert not any("5432" in line for line in host.rules("ipv4"))


def test_a_rule_for_the_wrong_family_is_refused(spec: ProductDeploymentSpec) -> None:
    """The two families have different chains AND different port matches
    (`--ctorigdstport` on the DNATed v4 path, `--dport` on the v6 INPUT path),
    so a mix-up is never a harmless one."""
    from dotmac_deployment_foundation.ingress import FirewallRule

    ipv4_rule = FirewallRule(
        family="ipv4",
        chain="DOCKER-USER",
        protocol="tcp",
        host_port=9001,
        action="DROP",
        source_set="",
        terminal=True,
    )
    host = FakeHost()
    with pytest.raises(StepFailed):
        _effects(host, spec).replace_rules("ipv6", "INPUT", [ipv4_rule])
    assert host.rules("ipv6") == [], "refused, and nothing was written first"


# ── preservation: the property this provider exists for ─────────────────────


def test_replace_rules_leaves_a_foreign_rule_alone(
    spec: ProductDeploymentSpec,
) -> None:
    from dotmac_deployment_foundation.policy import build_firewall_plan

    host = FakeHost(v4=[FOREIGN])
    rules = [r for r in build_firewall_plan(spec) if r.family == "ipv4"]
    _effects(host, spec).replace_rules("ipv4", "DOCKER-USER", rules)
    assert FOREIGN in host.rules("ipv4")


def test_replace_rules_replaces_only_our_own_earlier_rules(
    spec: ProductDeploymentSpec,
) -> None:
    from dotmac_deployment_foundation.policy import build_firewall_plan

    stale = f'-A DOCKER-USER -p tcp --dport 1 -m comment --comment "{OWNER}" -j DROP'
    host = FakeHost(v4=[FOREIGN, stale])
    rules = [r for r in build_firewall_plan(spec) if r.family == "ipv4"]
    _effects(host, spec).replace_rules("ipv4", "DOCKER-USER", rules)
    assert FOREIGN in host.rules("ipv4")
    assert stale not in host.rules("ipv4")


def test_rollback_restores_our_rules_and_keeps_a_MID_RUN_foreign_rule(
    spec: ProductDeploymentSpec, tmp_path: Path
) -> None:
    """The case a snapshot-replay implementation gets wrong.

    The foreign rule does not exist when the snapshot is taken; it appears
    while the transaction is running. Flushing the chain and replaying the
    snapshot would delete it, and the operator whose rule vanished would have
    no way to attribute the loss.
    """
    host = FakeHost()
    host.on_apply = lambda h: h.chains["ipv4"].append(FOREIGN)
    # Force verification to fail so the transaction rolls back: the socket
    # observation no longer matches the declared private publication.
    host.sockets = ""
    transaction = ExposureTransaction(
        spec=spec, effects=_effects(host, spec), lock_directory=tmp_path
    )
    with pytest.raises(PreconditionFailed):
        transaction.run()
    assert transaction.rolled_back is True
    assert FOREIGN in host.rules("ipv4"), "a mid-run foreign rule was destroyed"
    assert not [line for line in host.rules("ipv4") if OWNER in line]


def test_rollback_restores_rules_we_owned_BEFORE_the_transaction(
    spec: ProductDeploymentSpec, tmp_path: Path
) -> None:
    """Ours before, ours after. Preservation is not "delete everything we see"
    — a rule this product legitimately had must come back."""
    prior = (
        "-A DOCKER-USER -p tcp -m conntrack --ctorigdstport 7000 "
        f'-m comment --comment "{OWNER}" -j DROP'
    )
    host = FakeHost(v4=[FOREIGN, prior])
    host.sockets = ""
    transaction = ExposureTransaction(
        spec=spec, effects=_effects(host, spec), lock_directory=tmp_path
    )
    with pytest.raises(PreconditionFailed):
        transaction.run()
    assert FOREIGN in host.rules("ipv4")
    assert any(_same_rule(prior, line) for line in host.rules("ipv4"))


def test_nothing_is_ever_flushed(spec: ProductDeploymentSpec, tmp_path: Path) -> None:
    """The sensitivity proof for the whole design.

    Every other preservation test would still pass against an implementation
    that flushed and replayed perfectly on a quiet host. This one fails the
    moment a flush appears, whatever it does afterwards.
    """
    host = FakeHost(v4=[FOREIGN])
    host.sockets = ""
    with pytest.raises(PreconditionFailed):
        ExposureTransaction(
            spec=spec, effects=_effects(host, spec), lock_directory=tmp_path
        ).run()
    forbidden = {"-F", "--flush", "-X", "--delete-chain", "iptables-restore"}
    for argv in host.calls:
        assert not forbidden & set(argv), f"chain-wide operation used: {argv}"
        assert not Path(argv[0]).name.endswith("-restore")


def test_deletes_are_by_argument_never_by_index(
    spec: ProductDeploymentSpec, tmp_path: Path
) -> None:
    """An index shifts when anything else in the chain changes, so a
    concurrent foreign insert makes an index-based delete remove the wrong
    rule — the exact failure this file exists to prevent."""
    host = FakeHost(v4=[FOREIGN])
    host.sockets = ""
    with pytest.raises(PreconditionFailed):
        ExposureTransaction(
            spec=spec, effects=_effects(host, spec), lock_directory=tmp_path
        ).run()
    for argv in host.calls:
        if len(argv) > 3 and argv[1] == "-D":
            assert not argv[3].isdigit(), f"index-based delete: {argv}"


# ── the transaction, driven through the real provider ───────────────────────


def test_a_verifying_run_applies_reobserves_and_does_not_roll_back(
    spec: ProductDeploymentSpec, tmp_path: Path
) -> None:
    host = FakeHost()

    def install(h: FakeHost) -> None:
        # The apply is what makes the socket appear; before it the host has
        # nothing, exactly as a real recreate behaves.
        h.sockets = CONFORMING_SS

    host.sockets = ""
    host.on_apply = install
    transaction = ExposureTransaction(
        spec=spec, effects=_effects(host, spec), lock_directory=tmp_path
    )
    report = transaction.run()
    assert report.ok, [f.detail for f in report.refusals]
    assert transaction.rolled_back is False
    assert any(Path(c[0]).name == "docker" for c in host.calls)


def test_the_apply_command_forces_recreation(
    spec: ProductDeploymentSpec, tmp_path: Path
) -> None:
    """`docker compose restart` reuses the container it has, with the bindings
    it has, and a plain `up -d` will not recreate an unchanged image."""
    host = FakeHost()
    host.sockets = ""
    host.on_apply = lambda h: setattr(h, "sockets", CONFORMING_SS)
    ExposureTransaction(
        spec=spec, effects=_effects(host, spec), lock_directory=tmp_path
    ).run()
    compose = [c for c in host.calls if Path(c[0]).name == "docker"]
    assert compose
    assert all(part in compose[0] for part in APPLY_COMMAND)


def test_a_failed_apply_raises_and_does_not_pretend_to_have_verified(
    spec: ProductDeploymentSpec, tmp_path: Path
) -> None:
    host = FakeHost()
    host.compose_failure = True
    with pytest.raises(StepFailed):
        ExposureTransaction(
            spec=spec, effects=_effects(host, spec), lock_directory=tmp_path
        ).run()


def test_the_transaction_holds_the_product_lock_while_it_runs(
    spec: ProductDeploymentSpec, tmp_path: Path
) -> None:
    """An exposure change and a deployment must not interleave: one recreates
    the containers the other is measuring."""
    from dotmac_deployment_foundation.engine.lock import lock_path

    seen: list[bool] = []
    host = FakeHost()
    host.sockets = ""
    host.on_apply = lambda h: setattr(h, "sockets", CONFORMING_SS)
    effects = _effects(host, spec)
    original = effects.observe

    def watching():
        seen.append(lock_path(spec.product, directory=tmp_path).exists())
        return original()

    effects.observe = watching  # type: ignore[method-assign]
    ExposureTransaction(spec=spec, effects=effects, lock_directory=tmp_path).run()
    assert seen == [True, True]
