"""`ExposureEffects` against a real host, and the preservation property.

`ExposureTransaction` describes apply, re-observe and roll back. Until now
nothing implemented it against a host, so items 1-3 and 8 of the exposure
rehearsal were hand-driven — and a hand-driven equivalent proves the operator
can do it, not that the code can.

## The property that shapes this file

**A rollback must restore what THIS transaction changed, and nothing else.**

That is not a nicety. A `private` publication derives rules into `DOCKER-USER`
on IPv4 and `INPUT` on IPv6, and both chains are SHARED with everything else on
the host. The obvious implementation — snapshot the chain, flush it, replay the
snapshot — deletes any rule another process added while the transaction was
running. On a host carrying other work, "restore the snapshot" is a data-loss
bug wearing the word *restore*.

So this provider never flushes and never replays a whole chain. Every rule it
inserts carries an OWNERSHIP COMMENT::

    -m comment --comment dotmac-exposure:<product>

and every removal is a targeted ``-D`` of a rule bearing that comment. A rule
without it was not ours and is never touched, whenever it appeared. Ownership
lives in the rule itself rather than being inferred from a diff against a
snapshot, because a diff cannot distinguish "someone else added this" from "we
failed to record adding this", and those need opposite handling.

The snapshot is still taken, and is still what verification compares against.
It is simply not the thing rollback replays.

## Deleting by argument, never by index

``iptables -D <chain> <n>`` is the tempting form and it is unsafe here: an
index shifts the moment anything else in the chain changes, so a concurrent
foreign insert makes an index-based delete remove somebody else's rule. Every
delete in this file replays the rule's own arguments instead.

## Why the runner is injected

The same reason ``ComposeHostEffects`` does it. Every method is a real command
against a real host, and a scripted fake runner turns the whole matrix — a
foreign rule appearing mid-transaction included — into ordinary unit tests
rather than disposable-VM exercises.
"""

from __future__ import annotations

import shlex
from collections.abc import Callable, Sequence
from pathlib import Path

from ..engine.run import CommandResult
from ..errors import StepFailed
from ..exposure import HostObservation, ObservedChain, observation_from_text
from ..ingress import FAMILIES, FirewallRule
from ..spec import ProductDeploymentSpec

__all__ = ["OWNERSHIP_PREFIX", "ComposeHostExposureEffects", "ownership_comment"]

#: The marker that makes a rule attributable. Prefixed rather than bare, so two
#: products sharing a host own their rules independently.
OWNERSHIP_PREFIX = "dotmac-exposure"

#: Same shape as `compose_host.Runner`, restated rather than imported: a
#: provider depends on the engine's RESULT type, not on a sibling provider.
Runner = Callable[..., CommandResult]


def ownership_comment(product: str) -> str:
    return f"{OWNERSHIP_PREFIX}:{product}"


def _port_match_args(rule: FirewallRule) -> list[str]:
    """The chain-correct port match, taken FROM the rendered rule.

    Re-derived from :meth:`FirewallRule.render` rather than re-implemented, so
    the rule this provider inserts cannot drift from the rule the plan
    describes. That drift is not hypothetical: a `--dport` in a `DOCKER-USER`
    rule is how a remapped publish came to be filtered by nothing at all.
    """
    rendered = rule.render()
    _, _, remainder = rendered.partition(f"-p {rule.protocol} ")
    if rule.source_set:
        remainder = remainder.replace(f"-s @SOURCE_SET:{rule.source_set}@", " ")
    remainder = remainder.replace(f"-j {rule.action}", " ")
    return shlex.split(remainder)


class ComposeHostExposureEffects:
    """:class:`ExposureEffects` for one product on one dedicated host."""

    def __init__(
        self,
        spec: ProductDeploymentSpec,
        *,
        deploy_dir: str | Path,
        runner: Runner,
        docker_bin: str = "docker",
        iptables_bin: str = "iptables",
        ip6tables_bin: str = "ip6tables",
        ss_bin: str = "ss",
        timeout_seconds: int = 60,
    ) -> None:
        self._spec = spec
        self._deploy_dir = Path(deploy_dir)
        self._runner = runner
        self._docker_bin = docker_bin
        self._binary = {"ipv4": iptables_bin, "ipv6": ip6tables_bin}
        self._save = {"ipv4": f"{iptables_bin}-save", "ipv6": f"{ip6tables_bin}-save"}
        self._ss_bin = ss_bin
        self._timeout = timeout_seconds

    # ── plumbing ────────────────────────────────────────────────────────────

    def _run(
        self, argv: Sequence[str], *, allow_failure: bool = False
    ) -> CommandResult:
        result = self._runner(list(argv), timeout=self._timeout, env=None, capture=True)
        if not result.ok and not allow_failure:
            raise StepFailed(
                "exposure",
                f"`{shlex.join(argv)}` failed ({result.exit_code}): "
                f"{(result.stderr or result.stdout).strip()[:400]}",
            )
        return result

    @property
    def comment(self) -> str:
        return ownership_comment(self._spec.product)

    # ── observation ─────────────────────────────────────────────────────────

    def observe(self) -> HostObservation:
        """Everything the verifier may reason from, read fresh each time.

        `closed_port_behaviour` is deliberately left `unknown`. How a host
        answers a stranger cannot be determined from inside it, and
        `conclude_binding` already treats `unknown` conservatively. A caller
        that measured it from an external vantage supplies it; this method does
        not invent one, because an invented value would make a probe
        conclusive that is not.
        """
        sockets = self._run([self._ss_bin, "-tlnp"], allow_failure=True)
        processes = self._run(["ps", "-eo", "pid,args"], allow_failure=True)
        saves: dict[str, str] = {}
        for family in FAMILIES:
            dump = self._run([self._save[family]], allow_failure=True)
            if dump.ok:
                saves[family] = dump.stdout
        return observation_from_text(
            socket_listing=sockets.stdout,
            process_listing=processes.stdout,
            iptables_save=saves,
        )

    # ── mutation ────────────────────────────────────────────────────────────

    def apply_compose(self, command: Sequence[str], *, timeout_seconds: int) -> None:
        argv = [
            self._docker_bin,
            "compose",
            "--project-name",
            self._spec.product,
            "--project-directory",
            str(self._deploy_dir),
            *command,
        ]
        result = self._runner(argv, timeout=timeout_seconds, env=None, capture=True)
        if not result.ok:
            raise StepFailed(
                "apply_compose",
                f"`{shlex.join(argv)}` failed ({result.exit_code}): "
                f"{(result.stderr or result.stdout).strip()[:400]}",
            )

    def _insert_argv(self, rule: FirewallRule) -> list[str]:
        return [
            self._binary[rule.family],
            "-A",
            rule.chain,
            "-p",
            rule.protocol,
            *_port_match_args(rule),
            *(["-s", f"@SOURCE_SET:{rule.source_set}@"] if rule.source_set else []),
            "-m",
            "comment",
            "--comment",
            self.comment,
            "-j",
            rule.action,
        ]

    def owned_rules(self, family: str, chain: str) -> list[str]:
        """The `-A` lines in `chain` carrying our comment, in chain order."""
        dump = self._run([self._save[family]], allow_failure=True)
        marker = self.comment
        return [
            line.strip()
            for line in dump.stdout.splitlines()
            if line.strip().startswith(f"-A {chain} ") and marker in line
        ]

    def _delete_owned(self, family: str, chain: str) -> int:
        """Remove only rules bearing our comment. Foreign rules are untouched."""
        removed = 0
        for line in self.owned_rules(family, chain):
            argv = [self._binary[family], *shlex.split(line)]
            argv[1] = "-D"
            self._run(argv, allow_failure=True)
            removed += 1
        return removed

    def replace_rules(
        self, family: str, chain: str, rules: Sequence[FirewallRule]
    ) -> None:
        """Make OUR rules in `chain` exactly `rules`, touching nothing else."""
        for rule in rules:
            if rule.family != family:
                raise StepFailed(
                    "replace_rules",
                    f"a {rule.family} rule was offered to the {family} chain; "
                    "the two families have different chains and different port "
                    "matches, so this is never a harmless mix-up",
                )
        self._delete_owned(family, chain)
        for rule in rules:
            self._run(self._insert_argv(rule))

    def restore_chains(self, chains: Sequence[ObservedChain]) -> None:
        """Roll back by restoring OUR rules, never by replaying a whole chain.

        The snapshot decides which of our rules should exist afterwards.
        Anything in it that is not ours is ignored — it was not ours to
        restore — and anything currently present that is not ours is left
        exactly where it is, whether it predates the transaction or arrived
        while it ran.
        """
        marker = self.comment
        wanted: dict[tuple[str, str], list[str]] = {}
        for chain in chains:
            key = (chain.family, chain.name)
            wanted.setdefault(key, [])
            wanted[key].extend(
                f"-A {chain.name} {rule.arguments}"
                for rule in chain.rules
                if marker in rule.arguments
            )
        for family, name in sorted(wanted):
            self._delete_owned(family, name)
            for line in wanted[(family, name)]:
                argv = [self._binary[family], *shlex.split(line)]
                argv[1] = "-A"
                self._run(argv, allow_failure=True)
