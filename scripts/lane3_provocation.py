#!/usr/bin/env python3
"""Item 8's provocation — a condition the apply path structurally cannot satisfy.

`provoked_rollback` has never been executed. `ExposureTransaction.run()` rolls
back automatically when `verify_exposure` refuses, so `transaction.rolled_back`
stays `None` on a clean run and the lane caps at 15 of 16. Nothing here calls
`_rollback`. **A rollback the runner invokes proves the rollback function runs;
only one triggered by a verification that genuinely failed proves the path.**

## What is induced, and why the apply cannot clear it

An `ip6tables DOCKER-USER` rule matching the descriptor's `private` port.

`ingress.FILTER_CHAIN` is a MEASURED table, not a convention:
`{"ipv4": "DOCKER-USER", "ipv6": "INPUT"}`. IPv4 to a published port is DNATed
and traverses `FORWARD`, which jumps `DOCKER-USER`, so a rule there fires. IPv6
is accepted by `docker-proxy` on the host and terminates on `INPUT`; `FORWARD`
is never traversed, `DOCKER-USER` is never jumped, and **a rule there can never
fire.**

So the transaction writes its IPv6 rules to `INPUT` and has no authority over
ip6tables `DOCKER-USER`. It cannot clear what is seeded there. `observe()` dumps
whole tables through `ip6tables-save`, `_verify_firewall` raises `inert_chain`,
`report.ok` is false, and `run()` rolls back on its own.

This is not a failure arranged to happen. It is a real production defect: two
such rules were found on a production host with zero packet counters while the
ports were open. **They read as containment and are not.** Reproducing that is
worth doing permanently rather than as a rehearsal fixture.

## The counterfactual, which is what makes it a provocation at all

The criteria's test is: with the rollback path removed, would the run leave the
host changed? **Yes.** By the time verification refuses, the compose stack is up
and both filter chains have been rewritten. If the answer were "nothing", the
provocation would have proved nothing.

## Why the seeds are not collection, and why one of them leaves the library

The runner's invariant is that every host MUTATION originates through the
library. These are mutations, so they are named acts here rather than something
a collector does on the way past — if seeding blurred into collecting, the
mutation/observation split would become the hole its critics expect.

`arm_inert_chain` goes through `effects.replace_rules`, which is the library.
Rules it writes carry this product's ownership comment, which is harmless for
the refusal — `inert_chain` asks whether ANY rule matches the port, not who owns
it — and useful afterwards, because the rollback's `restore_chains` removes what
the snapshot did not contain.

`seed_foreign_rules` CANNOT go through the library, and the reason is structural
rather than convenient: every library write carries our ownership comment, and
"foreign" means precisely "not ours". A library that could seed a foreign rule
would be a library that can forge another owner's rules. So it issues the
commands directly, through the same runner the effects use, and says so.

Foreign rules exist to stop the restoration check passing vacuously. The
comparison is `foreign_before - foreign_after`, and the receipt renders
`foreign rules lost: none` — which **reads identically whether five rules were
preserved or zero rules existed**. Seeding both families is deliberate: a v4-only
seed leaves the v6 restoration unproven.
"""

from __future__ import annotations

import shlex
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from dotmac_deployment_foundation import ingress
from dotmac_deployment_foundation.errors import DeploymentFoundationError
from dotmac_deployment_foundation.exposure import foreign_rules
from dotmac_deployment_foundation.ingress import FirewallRule

#: The comment that makes a seeded rule FOREIGN. It must not be this product's
#: ownership comment, or `foreign_rules` will not see it and the vacuity guard
#: it exists to close stays open.
FOREIGN_OWNER: Final = "lane3-rehearsal-foreign-seed"

#: Ports the seeded foreign rules match. Deliberately outside the descriptor's
#: declared set, so a seed can never be mistaken for a rule the transaction was
#: supposed to write.
FOREIGN_PORTS: Final[dict[str, int]] = {"ipv4": 29001, "ipv6": 29002}


class ProvocationError(DeploymentFoundationError):
    """The provocation could not be established, so item 8 must not be claimed.

    Raised rather than swallowed: an unarmed provocation produces a clean run,
    a clean run produces `rolled_back is None`, and item 8 then fails for a
    reason that looks exactly like the defect this repairs.
    """


@dataclass(frozen=True, slots=True)
class SeededRule:
    """One foreign rule, and the argv that removes it again."""

    family: str
    chain: str
    arguments: str
    insert_argv: tuple[str, ...]
    delete_argv: tuple[str, ...]


def _binary(family: str) -> str:
    return "iptables" if family == "ipv4" else "ip6tables"


def _foreign_argv(family: str, verb: str) -> tuple[str, ...]:
    port = FOREIGN_PORTS[family]
    chain = ingress.FILTER_CHAIN[family]
    return (
        _binary(family),
        verb,
        chain,
        "-p",
        "tcp",
        *shlex.split(ingress.port_match(chain, port)),
        "-m",
        "comment",
        "--comment",
        FOREIGN_OWNER,
        "-j",
        "ACCEPT",
    )


def seed_foreign_rules(runner, *, timeout_seconds: int = 60) -> tuple[SeededRule, ...]:
    """Put a rule belonging to nobody in each family's filter chain.

    Returns what was seeded so the caller can withdraw it. Both families,
    always: `foreign_before - foreign_after` over an empty set is a comparison
    that ranges over nothing, and a v4-only seed proves nothing about v6.
    """
    seeded: list[SeededRule] = []
    for family in sorted(ingress.FILTER_CHAIN):
        insert = _foreign_argv(family, "-I")
        delete = _foreign_argv(family, "-D")
        result = runner(list(insert), timeout=timeout_seconds, env=None, capture=True)
        if not result.ok:
            for done in reversed(seeded):
                runner(
                    list(done.delete_argv),
                    timeout=timeout_seconds,
                    env=None,
                    capture=True,
                )
            raise ProvocationError(
                f"could not seed the foreign {family} rule "
                f"({' '.join(insert)}): {result.stderr.strip() or 'no stderr'}. "
                "Without it the restoration comparison ranges over nothing and "
                "`foreign rules lost: none` means only that there was nothing "
                "to lose"
            )
        seeded.append(
            SeededRule(
                family=family,
                chain=ingress.FILTER_CHAIN[family],
                arguments=" ".join(insert[3:]),
                insert_argv=insert,
                delete_argv=delete,
            )
        )
    return tuple(seeded)


def withdraw_foreign_rules(
    runner, seeded: Sequence[SeededRule], *, timeout_seconds: int = 60
) -> None:
    """Remove the seeds. Best effort, because the rehearsal's verdict is already
    recorded by the time this runs and a failure here must not rewrite it."""
    for rule in reversed(tuple(seeded)):
        runner(list(rule.delete_argv), timeout=timeout_seconds, env=None, capture=True)


def inert_rule(port: int) -> FirewallRule:
    """The rule that cannot fire: IPv6, in `DOCKER-USER`, for a published port."""
    return FirewallRule(
        family="ipv6",
        chain=ingress.DOCKER_USER_CHAIN,
        protocol="tcp",
        host_port=port,
        action="ACCEPT",
        source_set="lane3-provocation",
        terminal=False,
    )


def provoke_apply_failure(effects, *, port: int) -> FirewallRule:
    """Arm the condition, through the library, and return what was armed.

    Named for what it does to the SYSTEM, not for what it does to the test: it
    induces a state the apply path cannot reconcile. The verification that
    follows is the system's own, and the rollback after it is the system's own
    response.
    """
    rule = inert_rule(port)
    effects.replace_rules("ipv6", ingress.DOCKER_USER_CHAIN, (rule,))
    return rule


def disarm_apply_failure(effects) -> None:
    """Take the inert rule back out, through the same seam that put it in."""
    effects.replace_rules("ipv6", ingress.DOCKER_USER_CHAIN, ())


def private_port(spec) -> int:
    """The descriptor's `private` port — the only binding `_verify_firewall`
    reaches, because `none` and `loopback` return early."""
    for role in spec.roles:
        for port in role.ports:
            if port.exposure == "private":
                return int(port.host)
    raise ProvocationError(
        "this descriptor declares no `private` port, so `_verify_firewall` "
        "returns early for every binding and no firewall refusal can be "
        "induced. Item 8 must not be claimed against it"
    )


def inside_source_set(spec) -> str:
    """The source-set NAME the descriptor's private port accepts.

    A name, never members: this package resolves nothing, and item 12's refusal
    compares names. Reading it from the descriptor rather than restating it is
    what keeps the accepted set and the probed set the same thing.
    """
    for role in spec.roles:
        for port in role.ports:
            if port.exposure == "private":
                declared = str(getattr(port, "source_set", "") or "")
                if declared:
                    return declared
    raise ProvocationError(
        "the descriptor's private port names no source set, so there is no "
        "accepted set for a privileged-vantage refusal to be about"
    )


def observed_foreign(observation, *, owner: str) -> set[str]:
    """The foreign rule set, as the transaction's own comparison sees it."""
    return {rule.arguments for rule in foreign_rules(observation, owner=owner)}


__all__ = [
    "FOREIGN_OWNER",
    "FOREIGN_PORTS",
    "ProvocationError",
    "SeededRule",
    "disarm_apply_failure",
    "inert_rule",
    "inside_source_set",
    "observed_foreign",
    "private_port",
    "provoke_apply_failure",
    "seed_foreign_rules",
    "withdraw_foreign_rules",
]
