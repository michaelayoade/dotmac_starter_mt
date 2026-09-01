"""Freeze a legacy deployment executor, prove it displaced, and receipt its removal.

The programme's scoreboard reads ZERO retired legacy executors. Every release,
guard and composed unit so far has added nothing to it, because a replacement
that has been adopted as declarative input and as a CI gate has proven nothing
about its ability to replace an executor. Only the removal counts, and only a
removal that can be checked afterwards counts twice.

This module is the machinery for that removal. It deletes nothing, disables
nothing and revokes nothing. It makes three things checkable:

1. **A per-product entrypoint-family INVENTORY.** Typed, declaring each
   entrypoint with its family, trigger, credential identity and disposition.
2. **A two-directional RATCHET per family.** Fails when a family's count rises
   AND when it falls without the baseline being lowered in the same change.
3. **A retirement RECEIPT schema.** What must be true, and provable, at the
   moment an executor is deleted.

## The danger this is shaped around

> Deleting scripts before this would remove rollback capability; leaving them
> active afterward creates two executors.

Both failure modes are real and opposite. The decisive rule between them is
that **a replacement is not adopted while the displaced executor can still act
normally** — so `frozen` is a real state with its own evidence, and `retired`
is reachable only through it.

## Absence is never a disposition

An entrypoint that nobody listed is UNMONITORED, not clean. Discovery walks the
tree per family; anything discovered and not declared FAILS. This is the whole
reason the inventory is checked rather than merely written: a census covering
`scripts/` alone is the classic miss, and this programme has already found a
disabled-but-installed systemd unit, a development Compose service holding
production credentials, and a static-sync script rsyncing a checkout into an
nginx root that served it ahead of the application.

## A product with no inventory is UNADOPTED, never zero

Scoring an unmeasured product zero would report the debt as retired. A declared
product without an inventory file abstains loudly and is reported as unadopted.

## Sibling repositories are read from immutable git objects

Hard rule 30 and ADR-0032. A sibling is measured at the exact commit its
baseline row names, never from whatever branch a colleague has checked out, and
when that commit is absent from the local clone the sweep abstains rather than
scoring it. The repository this module lives in is measured from its WORKING
TREE and is always enforced, because that tree is the thing under review.

    python scripts/executor_retirement.py --check
    python scripts/executor_retirement.py --write-baseline
    python scripts/executor_retirement.py --validate-receipt <path.toml>
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import importlib.metadata
import itertools
import json
import pathlib
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from typing import Any, Final

PROJECT_ROOT: Final[pathlib.Path] = pathlib.Path(__file__).resolve().parents[1]
INVENTORY_DIR: Final[pathlib.Path] = (
    PROJECT_ROOT / "docs" / "inventories" / "executor-retirement"
)
BASELINE_PATH: Final[pathlib.Path] = (
    PROJECT_ROOT / "docs" / "inventories" / "executor-retirement-baseline.json"
)

#: The repository this sweep lives in, measured from its working tree.
SELF_REPOSITORY: Final[str] = "dotmac_starter_mt"

#: The products that OWE an inventory, named so that not having one is a
#: reported state rather than silence. ERP is the named first adopter: its
#: `scripts/deploy.sh`, its direct GitHub deployment workflow, its host-side
#: source synchronization and the credentials those hold are the shape this
#: contract was built against. Sub follows with `deploy_production.sh`. Naming
#: them here is what makes UNADOPTED a measurement — remove a name and the
#: adoption ratchet fails, so a product cannot quietly stop being expected.
ADOPTION_TARGETS: Final[tuple[str, ...]] = (
    "dotmac_erp",
    "dotmac_starter_mt",
    "dotmac_sub",
    # Added 2026-08-31. `vendor-cp-prod` is a named production host that
    # retains a rollback credential at Wave 7C, and it sat outside this roster
    # — so it would have been SILENTLY UNMONITORED rather than reported
    # UNADOPTED, which is the roster reproducing the exact failure the code
    # below was written to prevent.
    #
    # The entry is the REPOSITORY that owes the inventory, not the host that
    # retains the credential. `measure()` resolves `<product>.toml`, so a host
    # identity here would name a file nobody can ever write and would report
    # UNADOPTED forever for the wrong reason. `vendor-cp-prod` belongs in this
    # product's own inventory, as the `host` on the credential's row and as a
    # `production_targets` entry.
    "dotmac_vendor_control_plane",
)

INVENTORY_SCHEMA: Final[str] = "ExecutorInventory.v1"
RECEIPT_SCHEMA: Final[str] = "ExecutorRetirementReceipt.v1"


# ── Entry-point families ────────────────────────────────────────────────────
#
# Enumerated by NAME, not by directory. Hard rule 25 / ADR-0018 §1. A guard
# scoped to one directory is a guard with a known hole, and this list is the
# hole's shape stated in advance.


@dataclass(frozen=True)
class Family:
    """One entry-point family, and what can be known about it from a tree."""

    name: str
    #: Repository-relative directories walked for this family. Empty means the
    #: family has no in-tree surface at all.
    roots: tuple[str, ...]
    #: Filename globs that make a file a member of this family.
    patterns: tuple[str, ...]
    #: Whether a tree can enumerate this family completely. `False` means the
    #: authoritative registration lives OUTSIDE the repository — on a host, in
    #: a secret store, in a third party's webhook configuration — so the
    #: declaration is the only evidence and the coverage block says so.
    tree_complete: bool
    #: Why the tree cannot enumerate it. Required exactly when
    #: `tree_complete` is False; an exclusion with no premise is not an
    #: exemption, it is an unmonitored region.
    incompleteness_premise: str = ""
    #: Also walk the repository ROOT, one level deep. A one-file deployer at
    #: the top of a repository is an entrypoint, and no directory root sees it.
    #: A recursive root walk would swallow every other family, so it is
    #: deliberately shallow.
    include_repository_root: bool = False


FAMILIES: Final[tuple[Family, ...]] = (
    Family(
        name="workflow",
        roots=(".github/workflows", ".forgejo/workflows", ".gitlab-ci"),
        patterns=("*.yml", "*.yaml"),
        tree_complete=True,
    ),
    Family(
        name="script",
        roots=("scripts", "bin", "deploy", "ops", "tools"),
        patterns=("*.sh", "*.bash", "*.zsh", "*.ps1"),
        tree_complete=True,
        include_repository_root=True,
    ),
    Family(
        name="cron",
        roots=("deploy/cron", "deploy/cron.d", "cron", "cron.d", "etc/cron.d"),
        patterns=("*",),
        tree_complete=False,
        incompleteness_premise=(
            "a crontab installed on a host has no in-tree artifact; "
            "/etc/cron.d/dotmac_erp_db_backup is real and is invisible to any "
            "repository walk"
        ),
    ),
    Family(
        name="systemd_unit",
        roots=("deploy/systemd", "systemd", "etc/systemd"),
        patterns=("*.service", "*.timer", "*.socket", "*.path"),
        tree_complete=False,
        incompleteness_premise=(
            "a unit installed under /etc/systemd/system has no in-tree "
            "artifact, and `dotmac-books.service` was found installed and "
            "disabled — disabled is not absent, and absent from the tree is "
            "not absent from the host"
        ),
    ),
    Family(
        name="ssh_credential",
        roots=(),
        patterns=(),
        tree_complete=False,
        incompleteness_premise=(
            "a deploy key or authorized_keys entry lives in a secret store and "
            "on a host, never in a tree; a credential that appeared in a tree "
            "would be a leak, not an inventory"
        ),
    ),
    Family(
        name="webhook",
        roots=(),
        patterns=(),
        tree_complete=False,
        incompleteness_premise=(
            "the registration lives in the SENDER's configuration — a GitHub "
            "repository setting, a provider console — so a receiver route in "
            "this tree is at most half the entrypoint"
        ),
    ),
    Family(
        name="runtime_reactivation",
        roots=("deploy", "compose", "docker"),
        patterns=(
            "docker-compose*.yml",
            "docker-compose*.yaml",
            "compose*.yml",
            "compose*.yaml",
        ),
        tree_complete=False,
        include_repository_root=True,
        incompleteness_premise=(
            "a supervisor's ENABLEMENT lives on the host, not in the file that "
            "configures it: a unit can be installed and disabled, a Compose "
            "policy can name a service that is not running, and a @reboot "
            "crontab has no in-tree artifact at all"
        ),
    ),
    Family(
        name="manual_runbook",
        roots=("docs/runbooks", "docs/operations", "docs/ops", "runbooks"),
        patterns=("*.md", "*.rst", "*.txt"),
        tree_complete=False,
        incompleteness_premise=(
            "a procedure a person follows from memory, a wiki or a chat "
            "message is an executor with no artifact anywhere; only the "
            "written ones can be walked"
        ),
    ),
)

FAMILY_NAMES: Final[tuple[str, ...]] = tuple(family.name for family in FAMILIES)
FAMILY_BY_NAME: Final[dict[str, Family]] = {f.name: f for f in FAMILIES}


# ── Dispositions ────────────────────────────────────────────────────────────
#
# ADR-0018 §4 keeps two mechanisms DISTINCT: frozen debt, and a per-item verdict
# that something is genuinely fine as it stands. Here they are two disjoint sets
# with two different proofs:
#
#   BACKLOG_DISPOSITIONS  — frozen debt. Counted by the ratchet. Shrinks only
#                           by a recorded baseline change.
#   REVIEWED_DISPOSITIONS — a per-row verdict that this artifact is genuinely
#                           not a production deployment executor. Each carries
#                           a premise the sweep CHECKS, so the verdict cannot
#                           be bought by copying a comment.
#
# The word ADR-0018 uses for the first mechanism is deliberately absent from this
# module and from its data, and the ratchet test asserts that absence — which is
# why this comment describes the term rather than spelling it. If the two could
# be written interchangeably, a baseline entry would start reading as approval.

#: Debt. Every member can, or recently could, act on a production deployment.
BACKLOG_DISPOSITIONS: Final[dict[str, str]] = {
    "active_executor": (
        "can act on a declared production target right now; this is the thing "
        "being displaced"
    ),
    "frozen": (
        "retained and still capable, but declared no longer invoked while the "
        "replacement accumulates its two controller receipts; the rollback "
        "path is deliberately intact"
    ),
    "displaced": (
        "both controller receipts exist and removal is authorized; the "
        "artifact is still present because removal is a separate change"
    ),
    "reactivation_capable": (
        "can return a declared executor to acting state with NO invocation — "
        "after a reboot, a daemon restart or a supervisor decision. Debt "
        "because a displaced executor that can resurrect itself was never "
        "displaced; requires `reactivates`, and every name in it must resolve"
    ),
}

#: Not debt, and not silence either. Each requires a machine-checked premise.
REVIEWED_DISPOSITIONS: Final[dict[str, str]] = {
    "not_an_executor": (
        "contains no deployment verb at all; checked, so the verdict cannot be "
        "asserted over a file that deploys"
    ),
    "non_production_executor": (
        "acts only on an ephemeral or local target; checked against the "
        "product's declared production targets, so a file naming one is "
        "refused this verdict"
    ),
    "retained_rollback": (
        "deliberately kept as the recovery path for a retirement; requires the "
        "identity of the retirement it protects, so the retention has an owner "
        "and an end"
    ),
    "reactivates_no_declared_executor": (
        "carries a live reactivation directive but can return no DECLARED "
        "executor; checked in both directions, so it is refused the moment "
        "either its own `reactivates` fills in or another row names it"
    ),
}

#: Terminal. Reachable only through `displaced`, and only with a receipt.
TERMINAL_DISPOSITIONS: Final[dict[str, str]] = {
    "retired": (
        "removed from the tree and from the host; requires the identity of the "
        "retirement receipt that proves how"
    ),
}

DISPOSITIONS: Final[dict[str, str]] = {
    **BACKLOG_DISPOSITIONS,
    **REVIEWED_DISPOSITIONS,
    **TERMINAL_DISPOSITIONS,
}

#: The order a legacy executor may move through. `active_executor` may not jump
#: to `retired`: that jump IS the failure mode where rollback capability leaves
#: with the script. Stated as data so the refusal has one place to live.
PERMITTED_TRANSITIONS: Final[dict[str, tuple[str, ...]]] = {
    "active_executor": ("frozen", "not_an_executor", "non_production_executor"),
    "frozen": ("active_executor", "displaced"),
    "displaced": ("frozen", "retired", "retained_rollback"),
    "retained_rollback": ("retired",),
    "retired": (),
    "not_an_executor": ("active_executor", "reactivation_capable"),
    "non_production_executor": ("active_executor",),
    # A reactivation mechanism is not on the deploy/redeploy path, so it never
    # passes through `displaced`. It leaves debt one of two ways: the executor
    # it could resurrect is gone, or the mechanism itself is. What ORDERS the
    # two is not this table — it is the retirement receipt, which refuses to
    # commit while any row naming the subject is unaccounted for.
    "reactivation_capable": ("reactivates_no_declared_executor", "retired"),
    "reactivates_no_declared_executor": ("reactivation_capable", "retired"),
}


# ── Deployment verbs ────────────────────────────────────────────────────────
#
# The structural test that makes `not_an_executor` an ENFORCEABLE premise
# rather than an assertion. A regex over content, deliberately: an executor is
# recognisable by what it commands, not by what it is called. `deploy.sh` is
# the name people expect; `sync-static.sh` is the one that rsynced a checkout
# into a live nginx root.

DEPLOYMENT_VERBS: Final[tuple[tuple[str, str], ...]] = (
    (r"docker[ -]compose\b", "compose topology control"),
    (r"\bdocker\s+(run|stack|service|swarm)\b", "container execution"),
    (r"\bsystemctl\b", "host service control"),
    (r"\bsupervisorctl\b", "host service control"),
    (r"\bkubectl\s+(apply|rollout|set|delete)\b", "cluster mutation"),
    (r"\bhelm\s+(upgrade|install)\b", "cluster mutation"),
    (r"\balembic\s+(upgrade|downgrade|stamp)\b", "schema mutation"),
    (r"\brsync\b", "host filesystem synchronization"),
    (r"\bscp\b", "host filesystem synchronization"),
    (r"\bssh\s+[^\s]", "remote execution"),
    # `git pull` and `git reset --hard` INTO A DEPLOYED CHECKOUT are how ERP's
    # host-side source synchronization mutates a running release. `git
    # checkout` is deliberately NOT here: in a CI runner it is how every job
    # starts, so including it made a release workflow that requires a rehearsal
    # inherit a "host source mutation" it never performs. A verb that fires on
    # ordinary CI is a verb that teaches reviewers to ignore the finding.
    (r"\bgit\s+pull\b", "host source mutation"),
    (r"\bgit\s+reset\s+--hard\b", "host source mutation"),
    (r"\bpg_restore\b", "database restoration"),
    (r"\bnginx\s+-s\s+reload\b", "ingress reload"),
    (r"\bcertbot\b", "certificate issuance"),
)

_VERB_PATTERNS: Final[tuple[tuple[re.Pattern[str], str], ...]] = tuple(
    (re.compile(pattern), reason) for pattern, reason in DEPLOYMENT_VERBS
)

#: Why a directory is never walked. Each premise is checkable by looking at the
#: directory, which is what ADR-0018 requires of an exclusion.
SKIPPED_DIRECTORY_NAMES: Final[frozenset[str]] = frozenset(
    {
        "__pycache__",  # build output
        ".venv",  # a vendored interpreter, not repository source
        "venv",
        "node_modules",  # JavaScript dependencies
        "site-packages",  # installed third-party code
        ".git",  # object storage
    }
)


#: A directive that lets a supervisor act with NOBODY invoking anything.
#
# The family is `runtime_reactivation` and NOT `restart_policy`, and the
# difference is the whole point rather than a naming preference. Docker's
# `restart: unless-stopped` normally restarts THE SAME CONTAINER, which is not
# a deployment at all — a family named after the policy would describe the
# wrong thing and sweep in every benign case. The property that makes one of
# these an executor is narrower: **it can reactivate a DISPLACED executor after
# a reboot.** So the directive below is only half the finding; the other half
# is `reactivates`, which says whether anything it could bring back is a
# declared executor. A directive with an empty `reactivates` is capability
# without a subject, and is recorded as exactly that.
#
# What this reframes is the RECEIPT. A retirement no longer owes only "the
# artifact is gone"; it owes "the executor cannot autonomously return". Those
# are different claims, and only the second survives a reboot.
REACTIVATION_DIRECTIVES: Final[tuple[tuple[str, str], ...]] = (
    (
        r"""restart:\s*['"]?(always|unless-stopped|on-failure)""",
        "compose restart policy",
    ),
    (r"Restart\s*=\s*(always|on-failure|on-abnormal|on-watchdog)", "systemd restart"),
    (r"\[Install\]", "systemd enablement stanza"),
    (r"WantedBy\s*=", "systemd boot target"),
    (r"@reboot\b", "cron boot entry"),
)

_REACTIVATION_PATTERNS: Final[tuple[tuple[re.Pattern[str], str], ...]] = tuple(
    (re.compile(pattern), reason) for pattern, reason in REACTIVATION_DIRECTIVES
)

#: The families whose artifacts can carry a reactivation directive, and where a
#: `not_an_executor` verdict is therefore checked against one. Scoped rather
#: than universal: a shell script containing the string `@reboot` in a comment
#: is not a supervisor, and a guard that fired there would be overridden until
#: it stopped being read.
REACTIVATION_FAMILIES: Final[frozenset[str]] = frozenset(
    {"runtime_reactivation", "systemd_unit", "cron"}
)


#: The distribution whose entry point is the ONLY sanctioned way to mutate a
#: Compose topology. A DISTRIBUTION NAME is an identity: it is resolved against
#: installed metadata, so the question this answers is "was this reached
#: through that entry point", never "does this look like the sanctioned call".
SANCTIONED_DISTRIBUTION: Final[str] = "dotmac-deployment-foundation"

#: The verb reasons that constitute a Compose MUTATION. Derived from
#: `DEPLOYMENT_VERBS` rather than re-listed, so a new compose pattern is
#: covered the day it is added.
COMPOSE_MUTATION_REASONS: Final[frozenset[str]] = frozenset(
    {"compose topology control"}
)


def sanctioned_entry_points(
    distribution: str = SANCTIONED_DISTRIBUTION,
) -> frozenset[str] | None:
    """Console-script names the INSTALLED sanctioned distribution provides.

    `None` means the distribution is not installed, or installs no console
    script, and the question therefore cannot be answered — **UNMONITORED,
    never a pass.** The same rule the displacement window applies to its event
    source: a check that cannot establish its premise says so rather than
    returning the comfortable answer.

    WHY IDENTITY RATHER THAN INTENT. A sanctioned Compose mutation is one
    reached through this distribution's entry point, resolved from installed
    metadata — not from a path, a filename, a comment or a declared premise.
    The reason is a defect this module already produced: the verb detector read
    a USAGE COMMENT as a deployment and the edge resolver drew a call edge
    backwards, because a path mention is symmetric while invocation is not.
    "Is this the sanctioned compose call?" has exactly that shape — it asks
    about intent, which a tree cannot answer. "Was this reached through that
    entry point?" is a fact about topology.

    The console-script NAME is never written down in this module. It is read
    from metadata, and a test asserts the literal does not appear here — a
    hardcoded name would turn an identity check back into a string match.
    """
    try:
        dist = importlib.metadata.distribution(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None
    except Exception:  # pragma: no cover - a broken metadata tree
        return None
    names = {
        entry.name
        for entry in dist.entry_points
        if entry.group == "console_scripts" and entry.name
    }
    return frozenset(names) or None


def executable_text(text: str) -> str:
    """The artifact with its whole-line comments removed.

    CALLS, NOT MENTIONS — the same rule `credential_lifecycle_sweep.py` states
    for AST call sites, applied where no AST exists. This was not a theoretical
    tidy-up: `docker-compose.dev.yml` carries a usage comment reading
    `docker compose -f docker-compose.yml -f docker-compose.dev.yml up`, and on
    the first run of the eighth family the verb detector read it as a
    deployment and refused the file's verdict. A guard that fires on
    documentation gets overridden, and then it gets ignored.

    Deliberately conservative: only lines whose first non-blank character is
    `#`. An inline trailing comment keeps its line, so a real command followed
    by a note still counts, and a `#` inside a quoted value cannot silently
    delete the command beside it. Over-stripping would produce false
    NEGATIVES, which is the direction that hides an executor.
    """
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


def reactivation_directives(text: str) -> dict[str, str]:
    """Every directive in this text that lets a supervisor act unbidden."""
    found: dict[str, str] = {}
    body = executable_text(text)
    for pattern, reason in _REACTIVATION_PATTERNS:
        match = pattern.search(body)
        if match is not None:
            found[match.group(0).strip()] = reason
    return dict(sorted(found.items()))


def deployment_verbs(text: str) -> dict[str, str]:
    """Every deployment verb this text commands, mapped to why it counts."""
    found: dict[str, str] = {}
    body = executable_text(text)
    for pattern, reason in _VERB_PATTERNS:
        match = pattern.search(body)
        if match is not None:
            found[match.group(0).strip()] = reason
    return dict(sorted(found.items()))


# ── The typed inventory ─────────────────────────────────────────────────────


class InventoryError(ValueError):
    """The inventory could not be read as a contract. Never a soft warning."""


ENTRY_KEYS: Final[frozenset[str]] = frozenset(
    {
        "name",
        "family",
        "path",
        "trigger",
        "credential",
        "disposition",
        "premise",
        "targets",
        "reactivates",
        "delegates_to",
        "ssh_constraint",
        "rollback_for",
        "receipt",
        "observed_at",
        "observed_by",
        "host",
        "note",
    }
)

REQUIRED_ENTRY_KEYS: Final[tuple[str, ...]] = (
    "name",
    "family",
    "trigger",
    "credential",
    "disposition",
)

INVENTORY_KEYS: Final[frozenset[str]] = frozenset(
    {
        "schema",
        "product",
        "revision",
        "production_targets",
        "families_present",
        "families_absent",
        "family_absence",
        "entrypoint",
    }
)

#: A credential is named, never valued. This is the same rule ADR-0009 states
#: for settings, applied to an inventory: the row says WHICH credential an
#: entrypoint holds so that its revocation can be receipted, and holding the
#: value would make the inventory itself the leak.
CREDENTIAL_NONE: Final[str] = "none"

_SECRET_SHAPED: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}\b"),
    re.compile(r"(?i)\b(password|secret|token|api[_-]?key)\s*[:=]\s*\S"),
)


def secret_shaped(value: str) -> bool:
    """A value that looks like material rather than an identity name."""
    return any(pattern.search(value) for pattern in _SECRET_SHAPED)


@dataclass(frozen=True)
class Entrypoint:
    """One declared way a deployment can be caused to happen."""

    name: str
    family: str
    trigger: str
    credential: str
    disposition: str
    path: str = ""
    premise: str = ""
    targets: tuple[str, ...] = ()
    reactivates: tuple[str, ...] = ()
    delegates_to: str = ""
    ssh_constraint: SshConstraint | None = None
    rollback_for: str = ""
    receipt: str = ""
    observed_at: str = ""
    observed_by: str = ""
    host: str = ""
    note: str = ""

    @property
    def is_backlog(self) -> bool:
        return self.disposition in BACKLOG_DISPOSITIONS

    def canonical(self) -> dict[str, Any]:
        """The digest-bearing form: sorted, value-free, spelling-independent."""
        return {
            "name": self.name,
            "family": self.family,
            "trigger": self.trigger,
            "credential": self.credential,
            "disposition": self.disposition,
            "path": self.path,
            "targets": sorted(self.targets),
            "reactivates": sorted(self.reactivates),
            "delegates_to": self.delegates_to,
            "ssh_constraint": (
                self.ssh_constraint.canonical() if self.ssh_constraint else None
            ),
        }


#: How an absence was established. The distinction is the entire ERP lesson:
#: `dotmac-books.service` was installed and DISABLED on the host and appears in
#: no tree, and `/etc/cron.d/dotmac_erp_db_backup` is real. A repository walk
#: that finds nothing has established that the REPOSITORY holds nothing, and a
#: product that reads that as "there is no such unit" has drawn the one
#: conclusion the evidence cannot support.
ABSENCE_SCOPES: Final[dict[str, str]] = {
    "repository_tree": (
        "the tree was walked and holds no member. Says NOTHING about any host"
    ),
    "host_observed": (
        "a named host was inspected by a named person at a named time and holds "
        "no member"
    ),
}


# ── SshCredentialConstraintV1 ───────────────────────────────────────────────
#
# v2 could COUNT an SSH key and could not CHARACTERISE it. ERP's census is the
# live instance: eight root keys on the production host, none carrying `from=`,
# `command=` or `restrict`, and the deployment authority is those keys rather
# than any workflow. A contract that records "there are eight" and cannot say
# what any of them may do has measured the wrong thing.
#
# WHY THIS BELONGS IN THE RETIREMENT CONTRACT AND NOT IN A HARDENING DOCUMENT.
# This model requires the legacy executor's BYTES be retained rather than
# deleted — that is the whole point of `retained_rollback`, and it means a
# rollback credential survives every retirement. A retained key that can open
# an interactive root shell is not a rollback path. It is the executor still
# being reachable by hand, which is the second failure mode this ADR exists to
# prevent, arriving through the door the retirement itself held open.
#
# So the gate is narrow and it is on `retained_rollback`: source-restricted,
# forced-command-only, and incapable of an interactive shell. All three proven
# rather than asserted, and each independently, because a detector that fires
# only when everything is wrong passes the realistic case — one protection
# quietly dropped.
#
# INCAPABLE OF AN INTERACTIVE SHELL IS A CONJUNCTION, NOT A FLAG. It is
# `restrict`, AND a forced command, AND no pty. A key holding two of the three
# is not two-thirds safe; it is a key somebody can get a shell on. So each is
# its own named refusal, and the message says WHICH condition failed rather
# than that the key is "unsafe".
#
# AND THE EVIDENCE IS PART OF THE PROPERTY. Every clause above is a claim about
# a host, and a claim about a host that was not read on a host is a claim about
# nothing. `evidence_scope` is the SAME two-value vocabulary as `ABSENCE_SCOPES`
# above, for the same reason: `repository_tree` establishes what a checkout
# holds and says NOTHING about any host. The census may record either — a key
# committed to a tree is honestly characterised from that tree — but the
# retained-rollback gate requires `host_observed` on the host the row itself
# names, because absence of an observation must not read as satisfaction of the
# constraint.

#: A digest, never the command string. A changed forced command must be
#: DETECTABLE; a string invites a near-match being waved through in review, and
#: "close enough" is how `command="/usr/bin/deploy"` becomes
#: `command="/usr/bin/deploy --shell"`.
FORCED_COMMAND_NONE: Final[str] = "none"

#: Named rather than omitted. An unrestricted key must SAY it is unrestricted:
#: absence is never a disposition, and a blank field reads as "not looked at".
SOURCE_UNRESTRICTED: Final[str] = "unrestricted"

PERMISSION_STATES: Final[tuple[str, ...]] = ("denied", "permitted")
RESTRICT_STATES: Final[tuple[str, ...]] = ("present", "absent")

#: Derived from `ABSENCE_SCOPES`, never re-typed. One scope vocabulary for the
#: whole contract: a second copy is how the two meanings drift apart, and the
#: distinction between "the tree holds none" and "the host holds none" is the
#: single most load-bearing thing this module knows.
EVIDENCE_SCOPES: Final[tuple[str, ...]] = tuple(sorted(ABSENCE_SCOPES))

#: The scope a retained rollback key's constraint must carry. A restriction
#: lives in a host's `authorized_keys`; a tree cannot establish it.
EVIDENCE_HOST_OBSERVED: Final[str] = "host_observed"

#: Fillers that pass `is a non-empty string` and point at nothing. A required
#: field satisfied by `unknown` is worse than a missing one, because it reads
#: as an answer. These are refused in the coordinate fields, where the whole
#: purpose is that a later reader can go and look again.
PLACEHOLDER_COORDINATES: Final[frozenset[str]] = frozenset(
    {
        "-",
        "?",
        "assumed",
        "declared",
        "n/a",
        "na",
        "none",
        "not observed",
        "pending",
        "tbc",
        "tbd",
        "todo",
        "unknown",
        "unobserved",
    }
)

#: The coordinate a later reader re-resolves: which host, read when, by whom,
#: how, and at what scope. Checked harder than the rest of the row because it
#: is the half that makes the rest re-checkable.
SSH_COORDINATE_KEYS: Final[tuple[str, ...]] = (
    "host",
    "observed_at",
    "observed_by",
    "method",
)

#: Every permission `restrict` implies. OpenSSH's `restrict` means "deny all
#: current and future permissions", so a row claiming `restrict = present`
#: beside any `permitted` is describing a key that cannot exist — which means
#: it was hand-written rather than observed, and that is worth refusing on its
#: own.
RESTRICT_IMPLIED_DENIALS: Final[tuple[str, ...]] = (
    "pty",
    "agent_forwarding",
    "port_forwarding",
    "x11_forwarding",
)

SSH_CONSTRAINT_KEYS: Final[frozenset[str]] = frozenset(
    {
        "fingerprint",
        "principal",
        "source_restriction",
        "forced_command_digest",
        "restrict",
        "pty",
        "agent_forwarding",
        "port_forwarding",
        "x11_forwarding",
        "evidence_scope",
        "host",
        "observed_at",
        "observed_by",
        "method",
        "note",
    }
)

SSH_CONSTRAINT_REQUIRED: Final[tuple[str, ...]] = (
    "fingerprint",
    "principal",
    "source_restriction",
    "forced_command_digest",
    "restrict",
    "pty",
    "agent_forwarding",
    "port_forwarding",
    "x11_forwarding",
    "evidence_scope",
    "host",
    "observed_at",
    "observed_by",
    "method",
)


@dataclass(frozen=True)
class SshConstraint:
    """What one authorized key may actually do, as observed on a named host."""

    fingerprint: str
    principal: str
    source_restriction: str
    forced_command_digest: str
    restrict: str
    pty: str
    agent_forwarding: str
    port_forwarding: str
    x11_forwarding: str
    evidence_scope: str
    host: str
    observed_at: str
    observed_by: str
    method: str
    note: str = ""

    @property
    def source_restricted(self) -> bool:
        return self.source_restriction != SOURCE_UNRESTRICTED

    @property
    def forced_command_only(self) -> bool:
        return self.forced_command_digest != FORCED_COMMAND_NONE

    @property
    def restricted(self) -> bool:
        return self.restrict == "present"

    @property
    def pty_denied(self) -> bool:
        """Named on its own even though `restrict` implies it at parse.

        The gate must not borrow one of its own conditions from a different
        function's invariant. If `parse_ssh_constraint`'s contradiction check
        were ever relaxed, a gate that leaned on it would go on passing while
        one third of the conjunction it claims to enforce had quietly died.
        """
        return self.pty == "denied"

    @property
    def host_observed(self) -> bool:
        return self.evidence_scope == EVIDENCE_HOST_OBSERVED

    def canonical(self) -> dict[str, str]:
        """Part of the census digest, so LOOSENING a key moves the digest.

        Without this a key could be quietly unrestricted between two receipts
        and every recorded digest would still match.
        """
        return {
            "fingerprint": self.fingerprint,
            "principal": self.principal,
            "source_restriction": self.source_restriction,
            "forced_command_digest": self.forced_command_digest,
            "restrict": self.restrict,
            "pty": self.pty,
            "agent_forwarding": self.agent_forwarding,
            "port_forwarding": self.port_forwarding,
            "x11_forwarding": self.x11_forwarding,
        }


def parse_ssh_constraint(row: Any, where: str) -> SshConstraint:
    """Read one `SshCredentialConstraintV1`, refusing anything untyped."""
    if not isinstance(row, dict):
        raise InventoryError(f"{where}: `ssh_constraint` must be a table")
    unknown = sorted(set(row) - SSH_CONSTRAINT_KEYS)
    if unknown:
        raise InventoryError(f"{where}: ssh_constraint unknown key(s) {unknown}")
    for key in SSH_CONSTRAINT_REQUIRED:
        if not isinstance(row.get(key), str) or not row[key]:
            raise InventoryError(
                f"{where}: ssh_constraint `{key}` is required. An unstated "
                "restriction reads as 'nobody looked', which is exactly the "
                "state eight unrestricted root keys were in"
            )
    for key in ("pty", *RESTRICT_IMPLIED_DENIALS[1:]):
        if row[key] not in PERMISSION_STATES:
            raise InventoryError(
                f"{where}: ssh_constraint `{key}` must be one of "
                f"{list(PERMISSION_STATES)}; found {row[key]!r}"
            )
    if row["restrict"] not in RESTRICT_STATES:
        raise InventoryError(
            f"{where}: ssh_constraint `restrict` must be one of "
            f"{list(RESTRICT_STATES)}"
        )
    if not row["fingerprint"].startswith("SHA256:"):
        raise InventoryError(
            f"{where}: ssh_constraint `fingerprint` must be the SHA256 form "
            "(`ssh-keygen -lf`); a comment or a filename does not identify a key"
        )
    digest = row["forced_command_digest"]
    if digest != FORCED_COMMAND_NONE and not re.fullmatch(
        r"sha256:[0-9a-f]{64}", digest
    ):
        raise InventoryError(
            f"{where}: ssh_constraint `forced_command_digest` must be "
            f"`sha256:<64 hex>` or the literal {FORCED_COMMAND_NONE!r}. The "
            "DIGEST and not the command string, so a changed forced command is "
            "detectable rather than a near-match somebody waved through"
        )
    for key in ("source_restriction", "method", "note"):
        value = row.get(key, "")
        if value and secret_shaped(value):
            raise InventoryError(
                f"{where}: ssh_constraint `{key}` holds a value-shaped string. "
                "A key is identified by FINGERPRINT here; material never "
                "appears in an inventory"
            )
    if row["evidence_scope"] not in EVIDENCE_SCOPES:
        raise InventoryError(
            f"{where}: ssh_constraint `evidence_scope` must be one of "
            f"{list(EVIDENCE_SCOPES)}; found {row['evidence_scope']!r}. The same "
            "vocabulary as a family absence, because it carries the same "
            "distinction: a tree walk says NOTHING about any host"
        )
    for key in SSH_COORDINATE_KEYS:
        if row[key].strip().lower() in PLACEHOLDER_COORDINATES:
            raise InventoryError(
                f"{where}: ssh_constraint `{key}` is {row[key]!r}, which points "
                "at nothing. A coordinate a later reader cannot re-resolve is "
                "not weaker evidence, it is none — and a required field "
                "satisfied by a filler reads as an answer, which is worse than "
                "leaving it out"
            )
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", row["observed_at"]):
        raise InventoryError(
            f"{where}: ssh_constraint `observed_at` must be an ISO date "
            f"(`YYYY-MM-DD`); found {row['observed_at']!r}. A restriction is "
            "true at a MOMENT — without one, a reading from before the key was "
            "loosened is indistinguishable from a reading after it"
        )
    if row["restrict"] == "present":
        contradictions = sorted(
            key for key in RESTRICT_IMPLIED_DENIALS if row[key] != "denied"
        )
        if contradictions:
            raise InventoryError(
                f"{where}: ssh_constraint declares `restrict = present` and "
                f"also {contradictions} permitted. OpenSSH's `restrict` denies "
                "all current and future permissions, so this key cannot exist "
                "— the row was written rather than observed"
            )
    return SshConstraint(
        fingerprint=row["fingerprint"],
        principal=row["principal"],
        source_restriction=row["source_restriction"],
        forced_command_digest=row["forced_command_digest"],
        restrict=row["restrict"],
        pty=row["pty"],
        agent_forwarding=row["agent_forwarding"],
        port_forwarding=row["port_forwarding"],
        x11_forwarding=row["x11_forwarding"],
        evidence_scope=row["evidence_scope"],
        host=row["host"],
        observed_at=row["observed_at"],
        observed_by=row["observed_by"],
        method=row["method"],
        note=row.get("note", ""),
    )


def rollback_key_failures(entry: Entrypoint) -> list[str]:
    """What a RETAINED rollback key must prove, as independent named findings.

    Independent on purpose. A single "is this key safe" verdict fires only when
    everything is wrong and passes the realistic failure, which is one
    protection quietly dropped — so `restrict`, `from=` and `command=` are
    three findings, not one, and each message names the condition that failed
    rather than the key.

    Two groups, and the second is not decoration:

    **The capability conjunction.** Source-restricted, forced-command-only,
    `restrict`, and no pty. "Incapable of an interactive shell" is the AND of
    those, not any one of them: a key with a forced command but no `restrict`
    is not two-thirds safe, and neither is one with `restrict` recorded beside
    a permitted pty. `principal` is RECORDED and does not relax any of it; a
    non-root deploy user holding an unrestricted key is still the executor
    reachable by hand.

    **The evidence coordinate.** Every clause above is a claim about a host.
    Read from a checkout, it is a claim about a checkout — so the constraint
    must be `host_observed`, and observed on the host the row itself names.
    This is where absence must not read as satisfaction: a constraint with no
    reachable coordinate has not established the properties, and a gate that
    accepted it would report the retention safe on the strength of nobody
    having looked.
    """
    constraint = entry.ssh_constraint
    if constraint is None:
        return []
    failures: list[str] = []
    if not constraint.source_restricted:
        failures.append(
            f"{entry.name} is retained as a rollback path and its key "
            f"{constraint.fingerprint} is `{SOURCE_UNRESTRICTED}`. A retained "
            "rollback key must be SOURCE-RESTRICTED (`from=`): a key reachable "
            "from anywhere is not a rollback path"
        )
    if not constraint.forced_command_only:
        failures.append(
            f"{entry.name} is retained as a rollback path and its key "
            f"{constraint.fingerprint} runs no forced command. A retained "
            "rollback key must be FORCED-COMMAND-ONLY (`command=`): without one "
            "it executes whatever the client asks for"
        )
    if not constraint.restricted:
        failures.append(
            f"{entry.name} is retained as a rollback path and its key "
            f"{constraint.fingerprint} carries no `restrict`. A retained "
            "rollback key must be INCAPABLE OF AN INTERACTIVE SHELL, and "
            "`restrict` is what denies the pty, the forwarding and the agent "
            "that make one usable"
        )
    if not constraint.pty_denied:
        failures.append(
            f"{entry.name} is retained as a rollback path and its key "
            f"{constraint.fingerprint} PERMITS A PTY. Incapable of an "
            "interactive shell is a CONJUNCTION — `restrict`, a forced command "
            "and no pty — and a key holding two of the three is not two-thirds "
            "safe, it is a key somebody gets a shell on"
        )
    if not constraint.host_observed:
        failures.append(
            f"{entry.name} is retained as a rollback path and its key "
            f"{constraint.fingerprint} is characterised at scope "
            f"`{constraint.evidence_scope}`. A retained rollback key's "
            f"constraint must be `{EVIDENCE_HOST_OBSERVED}`: a restriction "
            "lives in a host's `authorized_keys`, so a checkout cannot "
            "establish one, and not having looked must not read as the key "
            "being restricted"
        )
    if entry.host and constraint.host != entry.host:
        failures.append(
            f"{entry.name} is retained as a rollback path on `{entry.host}` "
            f"and its key {constraint.fingerprint} was observed on "
            f"`{constraint.host}`. The evidence must be OBSERVED ON THE HOST "
            "THE ROW NAMES — a reading taken somewhere else describes a "
            "different `authorized_keys` and answers a different question"
        )
    return failures


ABSENCE_KEYS: Final[frozenset[str]] = frozenset(
    {"family", "scope", "observed_at", "observed_by", "method", "host", "note"}
)


@dataclass(frozen=True)
class FamilyAbsence:
    """A positive claim that a family has no members, and how it was reached."""

    family: str
    scope: str
    observed_at: str
    observed_by: str
    method: str
    host: str = ""
    note: str = ""


@dataclass(frozen=True)
class Inventory:
    """One product's declared entrypoint census at one immutable revision."""

    product: str
    revision: str
    production_targets: tuple[str, ...]
    families_present: tuple[str, ...]
    families_absent: tuple[str, ...]
    absences: tuple[FamilyAbsence, ...]
    entrypoints: tuple[Entrypoint, ...]

    def by_family(self) -> dict[str, tuple[Entrypoint, ...]]:
        grouped: dict[str, list[Entrypoint]] = {name: [] for name in FAMILY_NAMES}
        for entry in self.entrypoints:
            grouped.setdefault(entry.family, []).append(entry)
        return {name: tuple(rows) for name, rows in sorted(grouped.items())}

    def declared_paths(self) -> dict[str, set[str]]:
        paths: dict[str, set[str]] = {name: set() for name in FAMILY_NAMES}
        for entry in self.entrypoints:
            if entry.path:
                paths.setdefault(entry.family, set()).add(entry.path)
        return paths


def parse_inventory(text: str, *, source: str) -> Inventory:
    """Read an inventory, refusing anything it cannot type.

    Refuses an unknown schema, an unknown key at either level, a missing
    required key, an unknown family, an unknown disposition, a non-immutable
    revision, a secret-shaped credential, and a reviewed disposition with no
    premise. Every one of those, accepted quietly, converts the inventory from
    a contract into a description.
    """
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:  # pragma: no cover - message only
        raise InventoryError(f"{source}: not readable as TOML: {exc}") from exc

    unknown = sorted(set(raw) - INVENTORY_KEYS)
    if unknown:
        raise InventoryError(f"{source}: unknown top-level key(s): {unknown}")
    if raw.get("schema") != INVENTORY_SCHEMA:
        raise InventoryError(
            f"{source}: schema must be {INVENTORY_SCHEMA!r}; found "
            f"{raw.get('schema')!r}"
        )
    product = raw.get("product")
    if not isinstance(product, str) or not product:
        raise InventoryError(f"{source}: `product` must be a repository name")
    revision = raw.get("revision")
    if not isinstance(revision, str) or not _is_commit(revision):
        raise InventoryError(
            f"{source}: `revision` must be a 40-character commit; a branch name "
            "points at a different tree tomorrow (hard rule 30)"
        )

    targets = tuple(raw.get("production_targets") or ())
    if not all(isinstance(target, str) and target for target in targets):
        raise InventoryError(f"{source}: `production_targets` must be identity names")

    present = tuple(raw.get("families_present") or ())
    absent = tuple(raw.get("families_absent") or ())
    unknown_families = sorted((set(present) | set(absent)) - set(FAMILY_NAMES))
    if unknown_families:
        raise InventoryError(f"{source}: unknown family/families {unknown_families}")
    overlap = sorted(set(present) & set(absent))
    if overlap:
        raise InventoryError(f"{source}: {overlap} is both present and absent")
    missing_families = sorted(set(FAMILY_NAMES) - set(present) - set(absent))
    if missing_families:
        raise InventoryError(
            f"{source}: every family must be declared present or absent by "
            f"name; missing {missing_families}. A family nobody named is "
            "unmonitored, not empty"
        )

    absences: list[FamilyAbsence] = []
    for index, row in enumerate(raw.get("family_absence") or ()):
        where = f"{source}: family_absence[{index}]"
        if not isinstance(row, dict):
            raise InventoryError(f"{where}: must be a table")
        unknown_keys = sorted(set(row) - ABSENCE_KEYS)
        if unknown_keys:
            raise InventoryError(f"{where}: unknown key(s) {unknown_keys}")
        for key in ("family", "scope", "observed_at", "observed_by", "method"):
            if not isinstance(row.get(key), str) or not row[key]:
                raise InventoryError(f"{where}: `{key}` is required")
        if row["family"] not in FAMILY_BY_NAME:
            raise InventoryError(f"{where}: unknown family {row['family']!r}")
        if row["scope"] not in ABSENCE_SCOPES:
            raise InventoryError(
                f"{where}: unknown scope {row['scope']!r}; the scopes are "
                f"{sorted(ABSENCE_SCOPES)}"
            )
        if row["scope"] == "host_observed" and not row.get("host"):
            raise InventoryError(
                f"{where}: a host-observed absence names the host that was "
                "inspected. An unnamed host is not an observation"
            )
        absences.append(
            FamilyAbsence(
                family=row["family"],
                scope=row["scope"],
                observed_at=row["observed_at"],
                observed_by=row["observed_by"],
                method=row["method"],
                host=row.get("host", ""),
                note=row.get("note", ""),
            )
        )

    claimed = {absence.family for absence in absences}
    for family_name in sorted(absent):
        family = FAMILY_BY_NAME[family_name]
        if family.tree_complete:
            continue
        if family_name not in claimed:
            raise InventoryError(
                f"{source}: family `{family_name}` is declared absent, and a "
                "tree cannot enumerate it. Absence therefore needs a "
                "[[family_absence]] record naming who observed it, when, how "
                "and at what scope — otherwise this is unexamined, not empty"
            )
    stray = sorted(claimed - set(absent))
    if stray:
        raise InventoryError(
            f"{source}: [[family_absence]] declared for {stray}, which "
            "is/are not in `families_absent`"
        )

    entrypoints: list[Entrypoint] = []
    seen: set[str] = set()
    for index, row in enumerate(raw.get("entrypoint") or ()):
        where = f"{source}: entrypoint[{index}]"
        if not isinstance(row, dict):
            raise InventoryError(f"{where}: must be a table")
        unknown_keys = sorted(set(row) - ENTRY_KEYS)
        if unknown_keys:
            raise InventoryError(f"{where}: unknown key(s) {unknown_keys}")
        for key in REQUIRED_ENTRY_KEYS:
            if not isinstance(row.get(key), str) or not row[key]:
                raise InventoryError(
                    f"{where}: `{key}` is required and must be a non-empty "
                    "string. Absence is never a disposition"
                )
        if row["family"] not in FAMILY_BY_NAME:
            raise InventoryError(f"{where}: unknown family {row['family']!r}")
        if row["disposition"] not in DISPOSITIONS:
            raise InventoryError(
                f"{where}: unknown disposition {row['disposition']!r}; the "
                f"vocabulary is {sorted(DISPOSITIONS)}"
            )
        if secret_shaped(row["credential"]):
            raise InventoryError(
                f"{where}: `credential` holds a value-shaped string. A "
                "credential is NAMED here, never held (ADR-0009)"
            )
        if row["name"] in seen:
            raise InventoryError(f"{where}: duplicate entrypoint name {row['name']!r}")
        seen.add(row["name"])
        entrypoints.append(
            Entrypoint(
                name=row["name"],
                family=row["family"],
                trigger=row["trigger"],
                credential=row["credential"],
                disposition=row["disposition"],
                path=row.get("path", ""),
                premise=row.get("premise", ""),
                targets=tuple(row.get("targets") or ()),
                reactivates=tuple(row.get("reactivates") or ()),
                delegates_to=row.get("delegates_to", ""),
                ssh_constraint=(
                    parse_ssh_constraint(row["ssh_constraint"], where)
                    if "ssh_constraint" in row
                    else None
                ),
                rollback_for=row.get("rollback_for", ""),
                receipt=row.get("receipt", ""),
                observed_at=row.get("observed_at", ""),
                observed_by=row.get("observed_by", ""),
                host=row.get("host", ""),
                note=row.get("note", ""),
            )
        )

    for entry in entrypoints:
        if entry.family == "ssh_credential" and entry.ssh_constraint is None:
            raise InventoryError(
                f"{source}: {entry.name} is in the `ssh_credential` family and "
                "declares no `ssh_constraint`. Counting a key without saying "
                "what it may do is the v2 gap this closes: eight root keys were "
                "COUNTED on a production host and none of them characterised"
            )
        if entry.ssh_constraint is not None and entry.family != "ssh_credential":
            raise InventoryError(
                f"{source}: {entry.name} declares an `ssh_constraint` but is in "
                f"the `{entry.family}` family. A key's restriction state "
                "belongs to the key's own row"
            )

    names = {entry.name for entry in entrypoints}
    for entry in entrypoints:
        dangling = sorted(set(entry.reactivates) - names)
        if dangling:
            raise InventoryError(
                f"{source}: {entry.name} declares it can reactivate "
                f"{dangling}, which is/are not declared in this inventory. A "
                "reactivation pointing at nothing cannot be proven gone"
            )
        if entry.name in entry.reactivates:
            raise InventoryError(
                f"{source}: {entry.name} reactivates itself; that is a cycle, "
                "not a mechanism"
            )
        if entry.disposition == "reactivation_capable" and not entry.reactivates:
            raise InventoryError(
                f"{source}: {entry.name} is `reactivation_capable` and names "
                "nothing it can return. Capability with no subject is "
                "`reactivates_no_declared_executor`, which is a different and "
                "checkable claim"
            )
        if entry.reactivates and entry.disposition not in (
            "reactivation_capable",
            "retired",
        ):
            raise InventoryError(
                f"{source}: {entry.name} can reactivate {list(entry.reactivates)} "
                f"but is `{entry.disposition}`. A live mechanism that can "
                "return a declared executor is debt, not a reviewed verdict"
            )

    return Inventory(
        product=product,
        revision=revision,
        production_targets=targets,
        families_present=tuple(sorted(present)),
        families_absent=tuple(sorted(absent)),
        absences=tuple(absences),
        entrypoints=tuple(entrypoints),
    )


def _is_commit(value: str) -> bool:
    return len(value) == 40 and all(ch in "0123456789abcdef" for ch in value)


# ── Discovery: what the tree actually holds ─────────────────────────────────


def _walk(
    root: pathlib.Path, family: Family, base: str, *, shallow: bool = False
) -> list[str]:
    directory = root / base
    if not directory.is_dir():
        return []
    found: list[str] = []
    iterator = directory.iterdir() if shallow else directory.rglob("*")
    for path in sorted(iterator):
        if not path.is_file():
            continue
        if any(part in SKIPPED_DIRECTORY_NAMES for part in path.parts):
            continue
        if not any(fnmatch.fnmatch(path.name, glob) for glob in family.patterns):
            continue
        found.append(path.relative_to(root).as_posix())
    return found


def discover(root: pathlib.Path) -> dict[str, list[str]]:
    """Every in-tree artifact that belongs to a family, by family.

    Incomplete BY CONSTRUCTION for the four families whose registration lives
    outside a repository, which is why `Family.tree_complete` exists and why
    the coverage block prints it. Silent partial coverage is the failure this
    whole module is about.
    """
    found: dict[str, list[str]] = {}
    for family in FAMILIES:
        members: list[str] = []
        for base in family.roots:
            members.extend(_walk(root, family, base))
        if family.include_repository_root:
            members.extend(_walk(root, family, ".", shallow=True))
        found[family.name] = sorted(set(members))
    return found


def read_artifact(root: pathlib.Path, relative: str) -> str | None:
    path = root / relative
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _referenced_paths(text: str, known: frozenset[str]) -> set[str]:
    """In-tree artifacts this one invokes, resolved against what discovery found.

    A path match rather than a parser, deliberately: a workflow reaches a script
    through `run:`, a reusable workflow through `uses:`, and a composite action
    through neither, and every one of those spellings contains the callee's
    repository-relative path as a literal. Matching the path finds all three;
    parsing `uses:` alone finds one.
    """
    found: set[str] = set()
    # Comments stripped here TOO, and for a sharper reason than in the verb
    # detector. A path mention is SYMMETRIC; invocation is not. `docker-
    # compose.yml` carries a comment naming `scripts/deploy.sh` — the script
    # that operates ON it — and matching raw text drew the edge backwards, so
    # the topology inherited the verbs of the executor that deploys it. A
    # `uses:` or a `run:` line is not a comment, so every real edge survives.
    text = executable_text(text)
    for candidate in known:
        if candidate in text:
            found.add(candidate)
        elif candidate.startswith(".github/") and candidate[len(".github/") :] in text:
            # `uses: owner/repo/.github/workflows/x.yml@sha` and
            # `uses: ./.github/workflows/x.yml` both contain the tail.
            found.add(candidate)
    return found


def resolve_verbs(
    root: pathlib.Path,
    relative: str,
    known: frozenset[str],
    *,
    _seen: frozenset[str] = frozenset(),
) -> dict[str, str]:
    """Every deployment verb this artifact commands, DIRECTLY OR THROUGH A CALLEE.

    A caller inherits its callee's authority. `deployment-adopter.yml` runs no
    container itself; it dispatches `deployment-conformance.yml`, which does.
    Judging the caller on its own bytes would file it as harmless — which is
    the unchecked-caller-path hole stated as hard rule 37, seen from inside a
    guard rather than across a wire.

    Transitive with a visited set, so a pair of artifacts that reference each
    other terminates instead of recursing. The closure is bounded by the tree:
    an artifact this repository does not contain cannot be followed, and the
    coverage block says which families a tree cannot enumerate at all.
    """
    if relative in _seen:
        return {}
    text = read_artifact(root, relative)
    if text is None:
        return {}
    verbs = dict(deployment_verbs(text))
    seen = _seen | {relative}
    for callee in sorted(_referenced_paths(text, known) - seen):
        for verb, reason in resolve_verbs(root, callee, known, _seen=seen).items():
            verbs.setdefault(verb, f"{reason} (via {callee})")
    return dict(sorted(verbs.items()))


def known_paths(root: pathlib.Path) -> frozenset[str]:
    """Every in-tree artifact discovery can name, across every family."""
    return frozenset(
        relative for members in discover(root).values() for relative in members
    )


# ── Reconciliation: the declaration must survive contact with the tree ──────


def compose_mutations(inventory: Inventory, root: pathlib.Path) -> dict[str, list[str]]:
    """Every declared entrypoint that mutates a Compose topology IN-TREE.

    The identity test, and it is almost embarrassingly simple once stated that
    way: a SANCTIONED mutation happens inside the installed
    `dotmac-deployment-foundation`, which is not in the tree, so it never
    appears in a resolved verb set. An UNSANCTIONED one is in the tree, so it
    always does. Delegating to the entry point does not excuse a direct call
    beside it — the direct call is still in the tree and still resolves.

    This is a COUNTED measurement, not a refusal. ERP's live executors and the
    Starter's own `scripts/deploy.sh` are unsanctioned Compose mutations today;
    refusing them would make an honest census impossible on day one, which is
    the same mistake the SSH gate deliberately avoids. Wave 7C's "remove direct
    Compose mutation outside Foundation" is therefore expressed as driving this
    count to zero, which is what a two-directional ratchet is for.
    """
    known = known_paths(root)
    found: dict[str, list[str]] = {}
    for entry in inventory.entrypoints:
        if not entry.path:
            continue
        verbs = resolve_verbs(root, entry.path, known)
        mutations = sorted(
            verb
            for verb, reason in verbs.items()
            if any(marker in reason for marker in COMPOSE_MUTATION_REASONS)
        )
        if mutations:
            found[entry.name] = mutations
    return dict(sorted(found.items()))


def reconcile(inventory: Inventory, root: pathlib.Path) -> list[str]:
    """Findings where the declaration and the tree disagree.

    Four distinct wrongs, kept apart because they have different repairs:

    * **discovered but undeclared** — the unmonitored region. Fails.
    * **declared with a path that is gone** — either a retirement nobody
      receipted, or a typo. Fails either way; a row pointing at nothing cannot
      be re-checked next time.
    * **a reviewed verdict the artifact contradicts** — `not_an_executor` over
      a file that runs `docker compose`, or `non_production_executor` over one
      naming a production target. This is the check that makes the premise
      enforceable rather than decorative.
    * **a backlog row with no target and no host** — an executor that cannot
      say what it acts on cannot be proven displaced from it.
    """
    problems: list[str] = []
    discovered = discover(root)
    known = frozenset(
        relative for members in discovered.values() for relative in members
    )
    declared = inventory.declared_paths()

    for family_name, members in sorted(discovered.items()):
        for relative in members:
            if relative in declared.get(family_name, set()):
                continue
            problems.append(
                f"{inventory.product}: {family_name} entrypoint {relative} is in "
                "the tree and not in the inventory. Absence is never a "
                "disposition — declare it, with a family, trigger, credential "
                "and disposition"
            )

    for entry in inventory.entrypoints:
        if not entry.path:
            if FAMILY_BY_NAME[entry.family].tree_complete:
                problems.append(
                    f"{inventory.product}: {entry.name} is in the {entry.family} "
                    "family, which a tree can enumerate completely, so it must "
                    "carry the `path` that makes it re-checkable"
                )
            elif not entry.host:
                problems.append(
                    f"{inventory.product}: {entry.name} has neither `path` nor "
                    "`host`; an entrypoint with no coordinate cannot be "
                    "re-observed and is a claim, not a record"
                )
            continue

        text = read_artifact(root, entry.path)
        if text is None:
            problems.append(
                f"{inventory.product}: {entry.name} declares path {entry.path}, "
                "which is unreadable or absent. If it was REMOVED, that is a "
                "retirement and it needs a receipt and a lowered baseline in "
                "this same change"
            )
            continue

        directives = reactivation_directives(text)
        if entry.family in REACTIVATION_FAMILIES:
            if entry.disposition == "not_an_executor" and directives:
                problems.append(
                    f"{inventory.product}: {entry.name} is declared "
                    f"`not_an_executor` but carries {sorted(directives)}. A "
                    "supervisor that can act with nobody invoking it is not "
                    "nothing; the verdict is refused"
                )
            if entry.disposition == "reactivates_no_declared_executor" and (
                not directives
            ):
                problems.append(
                    f"{inventory.product}: {entry.name} claims "
                    "`reactivates_no_declared_executor` and carries no "
                    "reactivation directive at all. That is `not_an_executor`; "
                    "the weaker-sounding verdict must not become the place "
                    "everything lands"
                )

        verbs = resolve_verbs(root, entry.path, known)
        if entry.disposition == "not_an_executor":
            if verbs:
                problems.append(
                    f"{inventory.product}: {entry.name} is declared "
                    f"`not_an_executor` but commands {sorted(verbs)}. The "
                    "premise is checked, so this verdict is refused"
                )
        elif entry.disposition == "non_production_executor":
            named = sorted(
                target
                for target in inventory.production_targets
                if target and target in text
            )
            if named:
                problems.append(
                    f"{inventory.product}: {entry.name} is declared "
                    f"`non_production_executor` but names production target(s) "
                    f"{named}. The premise is checked, so this verdict is "
                    "refused"
                )

    for entry in inventory.entrypoints:
        if entry.disposition in REVIEWED_DISPOSITIONS and not entry.premise:
            problems.append(
                f"{inventory.product}: {entry.name} claims the reviewed "
                f"disposition `{entry.disposition}` with no premise. An "
                "exclusion whose premise is unstated is an unmonitored region "
                "(ADR-0018 §2)"
            )
        if entry.disposition == "reactivates_no_declared_executor":
            claimed_by = sorted(
                other.name
                for other in inventory.entrypoints
                if entry.name in other.reactivates
            )
            if claimed_by:
                problems.append(
                    f"{inventory.product}: {entry.name} claims to reactivate no "
                    f"declared executor, but {claimed_by} name(s) it. Checked "
                    "in BOTH directions, because a one-way check is satisfied "
                    "by not filling in your own field"
                )
        if entry.delegates_to:
            sanctioned = sanctioned_entry_points()
            if sanctioned is None:
                problems.append(
                    f"{inventory.product}: {entry.name} delegates to "
                    f"{entry.delegates_to!r}, and `{SANCTIONED_DISTRIBUTION}` "
                    "is not installed, so the claim cannot be established — "
                    "UNMONITORED, not a pass. Install the distribution where "
                    "this check runs"
                )
            elif entry.delegates_to not in sanctioned:
                problems.append(
                    f"{inventory.product}: {entry.name} delegates to "
                    f"{entry.delegates_to!r}, which is not a console script of "
                    f"`{SANCTIONED_DISTRIBUTION}` (it provides "
                    f"{sorted(sanctioned)}). Sanction is ENTRY-POINT IDENTITY "
                    "resolved from installed metadata, never a name that looks "
                    "right"
                )
        if entry.disposition == "retained_rollback":
            problems.extend(
                f"{inventory.product}: {failure}"
                for failure in rollback_key_failures(entry)
            )
        if entry.disposition == "retained_rollback" and not entry.rollback_for:
            problems.append(
                f"{inventory.product}: {entry.name} is retained as a rollback "
                "path but names no retirement it protects. A retention with no "
                "owner never ends"
            )
        if entry.disposition == "retired" and not entry.receipt:
            problems.append(
                f"{inventory.product}: {entry.name} is `retired` with no "
                "receipt identity. A removal nobody receipted is a deletion, "
                "and the scoreboard counts receipts"
            )
        if entry.is_backlog and not entry.targets and not entry.host:
            problems.append(
                f"{inventory.product}: {entry.name} is `{entry.disposition}` "
                "and names neither a target nor a host. An executor that "
                "cannot say what it acts on cannot be proven displaced from it"
            )

    for family in FAMILIES:
        rows = [e for e in inventory.entrypoints if e.family == family.name]
        if family.name in inventory.families_absent:
            if rows:
                problems.append(
                    f"{inventory.product}: family `{family.name}` is declared "
                    f"ABSENT and carries {len(rows)} entrypoint row(s)"
                )
            if discovered[family.name]:
                problems.append(
                    f"{inventory.product}: family `{family.name}` is declared "
                    f"ABSENT and the tree holds {discovered[family.name]}"
                )
            continue
        if not rows:
            problems.append(
                f"{inventory.product}: family `{family.name}` is declared "
                "PRESENT with no entrypoint rows. Declare it absent, with the "
                "observation that establishes the absence"
            )

    return sorted(problems)


# ── The two-directional ratchet, per family ─────────────────────────────────


def family_counts(inventory: Inventory) -> dict[str, dict[str, int]]:
    """Per family, per disposition, how many entrypoints are declared."""
    counts: dict[str, dict[str, int]] = {}
    for entry in inventory.entrypoints:
        counts.setdefault(entry.family, {})
        counts[entry.family][entry.disposition] = (
            counts[entry.family].get(entry.disposition, 0) + 1
        )
    return {
        family: dict(sorted(dispositions.items()))
        for family, dispositions in sorted(counts.items())
    }


def ratchet_family(
    live: dict[str, dict[str, int]],
    recorded: dict[str, dict[str, int]],
    where: str,
) -> list[str]:
    """Fail on a RISE and on a FALL that no baseline change accompanied.

    Per family and per disposition, because a family total alone hides the move
    that matters: an `active_executor` becoming a `frozen` one keeps the total
    still while changing everything about what may happen next.
    """
    problems: list[str] = []
    for family in sorted(set(live) | set(recorded)):
        live_row = live.get(family, {})
        recorded_row = recorded.get(family, {})
        for disposition in sorted(set(live_row) | set(recorded_row)):
            now = live_row.get(disposition, 0)
            was = recorded_row.get(disposition, 0)
            if now > was:
                problems.append(
                    f"{where}: {family}/{disposition} rose {was} -> {now}. A new "
                    "entrypoint or a new caller landed in this family; declare "
                    "it and record the count in the same change"
                )
            elif now < was:
                problems.append(
                    f"{where}: {family}/{disposition} fell {was} -> {now} "
                    "without the baseline moving. If you RETIRED it, lower the "
                    "baseline in the SAME change and attach the receipt; if you "
                    "did not, the detector stopped seeing it"
                )
    return problems


# ── Measurement across the fleet ────────────────────────────────────────────


@dataclass
class ProductMeasurement:
    """One product's inventory as it stands, plus what could not be read."""

    product: str
    revision: str = ""
    measured_from: str = ""
    counts: dict[str, dict[str, int]] = field(default_factory=dict)
    reconciliation: list[str] = field(default_factory=list)
    inventory_digest: str = ""
    families_present: tuple[str, ...] = ()
    families_absent: tuple[str, ...] = ()
    families_tree_complete: tuple[str, ...] = ()
    families_declaration_only: tuple[str, ...] = ()
    tree_only_absences: tuple[str, ...] = ()
    compose_mutations: dict[str, list[str]] = field(default_factory=dict)
    sanction_state: str = ""


def _git(repo_root: pathlib.Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:  # pragma: no cover - git absent
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout


def _head_revision(repo_root: pathlib.Path) -> str:
    found = _git(repo_root, "rev-parse", "HEAD")
    return (found or "").strip() or "0" * 40


def canonical_digest(payload: Any) -> str:
    """`sha256:<64 lowercase hex>` over a canonical JSON encoding.

    The prefixed spelling, matching `dotmac_deployment_foundation.digest`, so a
    digest crossing between the two is one value rather than two spellings of
    one. Sorted keys and tight separators, so the digest is a function of the
    content and not of the formatting.
    """
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def inventory_digest(inventory: Inventory) -> str:
    return canonical_digest(
        {
            "schema": INVENTORY_SCHEMA,
            "product": inventory.product,
            "revision": inventory.revision,
            "production_targets": sorted(inventory.production_targets),
            "families_present": sorted(inventory.families_present),
            "families_absent": sorted(inventory.families_absent),
            "absences": sorted(
                (
                    {
                        "family": a.family,
                        "scope": a.scope,
                        "observed_at": a.observed_at,
                        "observed_by": a.observed_by,
                        "host": a.host,
                    }
                    for a in inventory.absences
                ),
                key=lambda row: row["family"],
            ),
            "entrypoints": sorted(
                (entry.canonical() for entry in inventory.entrypoints),
                key=lambda row: row["name"],
            ),
        }
    )


def measure_product(
    product: str, inventory: Inventory, root: pathlib.Path, measured_from: str
) -> ProductMeasurement:
    return ProductMeasurement(
        product=product,
        revision=inventory.revision,
        measured_from=measured_from,
        counts=family_counts(inventory),
        reconciliation=reconcile(inventory, root),
        inventory_digest=inventory_digest(inventory),
        families_present=inventory.families_present,
        families_absent=inventory.families_absent,
        families_tree_complete=tuple(
            f.name
            for f in FAMILIES
            if f.tree_complete and f.name in inventory.families_present
        ),
        families_declaration_only=tuple(
            f.name
            for f in FAMILIES
            if not f.tree_complete and f.name in inventory.families_present
        ),
        tree_only_absences=tuple(
            sorted(
                absence.family
                for absence in inventory.absences
                if absence.scope == "repository_tree"
            )
        ),
        compose_mutations=compose_mutations(inventory, root),
        sanction_state=(
            "resolved" if sanctioned_entry_points() is not None else "unmonitored"
        ),
    )


def inventory_path(product: str) -> pathlib.Path:
    return INVENTORY_DIR / f"{product}.toml"


def measure(
    baseline: dict, inventory_dir: pathlib.Path | None = None
) -> tuple[dict[str, ProductMeasurement], list[str], list[str]]:
    """Measure what can be measured; name what cannot, and why.

    Returns `(measured, unadopted, unverified)`. The three states are separate
    on purpose: unadopted means the product has no inventory at all, unverified
    means an inventory whose revision does not match the tree it describes, and
    NEITHER is a number.
    """
    directory = inventory_dir or INVENTORY_DIR
    measured: dict[str, ProductMeasurement] = {}
    unadopted: list[str] = []
    unverified: list[str] = []
    rows: dict[str, dict] = baseline.get("products", {})

    for product in sorted(set(rows) | set(ADOPTION_TARGETS) | {SELF_REPOSITORY}):
        path = directory / f"{product}.toml"
        if not path.is_file():
            unadopted.append(
                f"{product}: no inventory at "
                f"{path.name} — UNADOPTED, which is not zero and not clean"
            )
            continue
        inventory = parse_inventory(path.read_text(encoding="utf-8"), source=path.name)
        if product == SELF_REPOSITORY:
            measured[product] = measure_product(
                product, inventory, PROJECT_ROOT, "working tree"
            )
            continue
        unverified.append(
            f"{product}: a sibling inventory is measured at its recorded "
            f"commit {inventory.revision} in that repository, not from this "
            "checkout — run the sweep there"
        )
    return measured, unadopted, unverified


def ratchet(
    measured: dict[str, ProductMeasurement], baseline: dict
) -> tuple[list[str], list[str]]:
    """Two-directional per product, per family, per disposition."""
    failures: list[str] = []
    abstentions: list[str] = []
    recorded: dict[str, dict] = baseline.get("products", {})

    for product, row in sorted(recorded.items()):
        live = measured.get(product)
        if live is None:
            abstentions.append(f"{product}: UNMEASURED this run")
            continue
        recorded_digest = row.get("inventory_digest")
        if recorded_digest and recorded_digest != live.inventory_digest:
            # Counts alone miss every change that alters a row's CONTENT
            # without altering how many rows there are — a target repointed, a
            # trigger changed, or (the case this was added for) an SSH key
            # quietly loosened. `SshCredentialConstraintV1` is part of the
            # canonical form precisely so that loosening moves this digest, and
            # a digest nothing compares is a digest that moves unobserved.
            failures.append(
                f"{product}: the census digest moved "
                f"{recorded_digest} -> {live.inventory_digest} without the "
                "baseline moving. A row changed content without changing the "
                "counts; re-record it in the SAME change"
            )
        failures.extend(live.reconciliation)
        failures.extend(ratchet_family(live.counts, row.get("families", {}), product))

        recorded_paths = row.get("unsanctioned_compose_mutation_paths")
        if recorded_paths is not None:
            live_paths = sorted(live.compose_mutations)
            gained = sorted(set(live_paths) - set(recorded_paths))
            lost = sorted(set(recorded_paths) - set(live_paths))
            # The SET, not the count. A swap — one path retired while another
            # gains the ability — leaves the count still and is exactly the
            # move that matters.
            if gained:
                failures.append(
                    f"{product}: {gained} gained the ability to mutate a "
                    f"Compose topology outside `{SANCTIONED_DISTRIBUTION}`. "
                    "Sanction is reached THROUGH that distribution's entry "
                    "point; anything else in the tree is unsanctioned"
                )
            if lost:
                failures.append(
                    f"{product}: {lost} no longer mutates a Compose topology "
                    "and the baseline still lists it. Wave 7C drives this set "
                    "to EMPTY, and each step is recorded in the change that "
                    "takes it"
                )

    for product in sorted(set(measured) - set(recorded)):
        failures.append(
            f"{product}: measured but absent from the baseline; a product "
            "nobody recorded is unmonitored, not clean"
        )
    return failures, abstentions


def ratchet_adoption(
    measured: dict[str, ProductMeasurement],
    unadopted: list[str],
    baseline: dict,
) -> list[str]:
    """The programme scoreboard, ratcheted in both directions.

    Two numbers, and they are the only two the programme is actually scored on:

    * **who has not adopted** — a product that adopts must be removed from the
      recorded list in the same change, and a product that quietly stops being
      expected must not be able to leave the list by being forgotten;
    * **how many executors are retired** — this is the scoreboard. It rises
      only with a receipt, and it may not fall at all: a `retired` row that
      disappears is a record being deleted, not an executor coming back.
    """
    problems: list[str] = []
    live_unadopted = sorted(line.split(":", 1)[0] for line in unadopted)
    recorded_unadopted = sorted(baseline.get("unadopted", []))
    if live_unadopted != recorded_unadopted:
        entered = sorted(set(live_unadopted) - set(recorded_unadopted))
        left = sorted(set(recorded_unadopted) - set(live_unadopted))
        problems.append(
            "adoption: the unadopted set drifted; "
            f"entered={entered} left={left}. A product that ADOPTED must be "
            "removed from the baseline in the same change; one that entered "
            "this state was never recorded as owing an inventory"
        )

    live_retired = sum(
        row.get("retired_total", 0)
        for row in (
            {"retired_total": sum(c.get("retired", 0) for c in m.counts.values())}
            for m in measured.values()
        )
    )
    recorded_retired = baseline.get("retired_total", 0)
    if live_retired > recorded_retired:
        problems.append(
            f"adoption: retired executors rose {recorded_retired} -> "
            f"{live_retired} without the baseline moving. A retirement is "
            "recorded with its receipt, in the same change"
        )
    elif live_retired < recorded_retired:
        problems.append(
            f"adoption: retired executors FELL {recorded_retired} -> "
            f"{live_retired}. The scoreboard does not go down; a `retired` row "
            "that vanished is a record being deleted"
        )
    return problems


def build_baseline(measured: dict[str, ProductMeasurement], previous: dict) -> dict:
    previous_products: dict[str, dict] = previous.get("products", {})
    products: dict[str, dict] = {}
    for product, live in sorted(measured.items()):
        products[product] = {
            "revision": live.revision,
            "measured_from": live.measured_from,
            "inventory_digest": live.inventory_digest,
            "families_present": list(live.families_present),
            "families_absent": list(live.families_absent),
            "absences_established_by_tree_walk_only": list(live.tree_only_absences),
            "families_tree_complete": list(live.families_tree_complete),
            "families_declaration_only": list(live.families_declaration_only),
            "families": live.counts,
            "unsanctioned_compose_mutations": len(live.compose_mutations),
            "unsanctioned_compose_mutation_paths": sorted(live.compose_mutations),
            "retired_total": sum(
                counts.get("retired", 0) for counts in live.counts.values()
            ),
        }
    # A product that could not be measured this run keeps its recorded row.
    # Dropping it would silently retire an executor nobody retired.
    for product, prior in sorted(previous_products.items()):
        products.setdefault(product, prior)
    return {
        "schema_version": 1,
        "inventory_schema": INVENTORY_SCHEMA,
        "receipt_schema": RECEIPT_SCHEMA,
        "families": {
            family.name: {
                "roots": list(family.roots),
                "patterns": list(family.patterns),
                "tree_complete": family.tree_complete,
                "incompleteness_premise": family.incompleteness_premise,
            }
            for family in FAMILIES
        },
        "sanctioned_compose_distribution": SANCTIONED_DISTRIBUTION,
        "dispositions": {
            "backlog": sorted(BACKLOG_DISPOSITIONS),
            "reviewed": sorted(REVIEWED_DISPOSITIONS),
            "terminal": sorted(TERMINAL_DISPOSITIONS),
        },
        "unadopted": sorted(
            product
            for product in ADOPTION_TARGETS
            if not inventory_path(product).is_file()
        ),
        "retired_total": sum(row.get("retired_total", 0) for row in products.values()),
        "products": products,
    }


def coverage(
    measured: dict[str, ProductMeasurement],
    unadopted: list[str],
    unverified: list[str],
) -> str:
    """State the bounds out loud.

    A bounded measurement that does not state its bounds reads as "covered
    everything", and four of the seven families here cannot be covered by a
    repository walk at all.
    """
    lines = ["COVERAGE"]
    for product, live in sorted(measured.items()):
        total = sum(sum(row.values()) for row in live.counts.values())
        lines.append(
            f"  {product} @ {live.revision} ({live.measured_from}): {total} "
            f"declared entrypoints across {len(live.counts)} families"
        )
        lines.append(
            f"    tree-enumerable: {', '.join(live.families_tree_complete) or 'none'}"
        )
        lines.append(
            "    DECLARATION-ONLY (a tree cannot enumerate these): "
            f"{', '.join(live.families_declaration_only) or 'none'}"
        )
        lines.append(
            "    declared ABSENT: " f"{', '.join(live.families_absent) or 'none'}"
        )
        if live.tree_only_absences:
            lines.append(
                "    absence established by TREE WALK ONLY (says nothing about "
                f"any host): {', '.join(live.tree_only_absences)}"
            )
        lines.append(
            "    unsanctioned Compose mutations: "
            f"{len(live.compose_mutations)}"
            + (f" — {sorted(live.compose_mutations)}" if live.compose_mutations else "")
        )
        if live.sanction_state != "resolved":
            lines.append(
                f"    SANCTION UNMONITORED: `{SANCTIONED_DISTRIBUTION}` is not "
                "installed here, so entry-point identity could not be resolved "
                "— this is not a pass"
            )
        lines.append(f"    inventory digest: {live.inventory_digest}")
    for line in unadopted:
        lines.append(f"  UNADOPTED {line}")
    for line in unverified:
        lines.append(f"  UNVERIFIED {line} — unmeasured, not zero")
    return "\n".join(lines)


# ── The retirement receipt ──────────────────────────────────────────────────
#
# A receipt commits at step 6 — the removal — and is the ONLY thing that moves
# the programme scoreboard. Governance ADR 0018's authority-cutover receipt is
# the model: value-free, identity names and digests only, a status vocabulary
# in which absence is not a status, and no field satisfiable by a summary
# rather than a measurement.
#
# There is deliberately NO REGISTRY here. Receipts are product-side artifacts;
# Governance owns the cross-repository envelope and the creation of that store
# is a separate decision. This module owns the SCHEMA and its refusals.

RECEIPT_STATUSES: Final[dict[str, str]] = {
    "proposed": "drafted; the removal has not happened",
    "committed": "the removal happened and this receipt is immutable",
    "superseded": (
        "corrected by a later receipt, which is named; a committed receipt is "
        "never edited in place"
    ),
}

RECEIPT_KEYS: Final[frozenset[str]] = frozenset(
    {
        "schema",
        "status",
        "receipt_id",
        "product",
        "subject",
        "controller_receipts",
        "removals",
        "zero_surface_guard",
        "displacement_window",
        "no_autonomous_return",
        "recovery_verdict",
        "retained_rollback",
        "superseded_by",
        "supersedes",
        "digest",
    }
)

#: Every class of thing a retirement must account for. A removal list that
#: omits a class present on the subject is a partial retirement presented as a
#: complete one, and a partial retirement is exactly how a second executor
#: survives — `dotmac-books.service` was installed and disabled, not removed.
REMOVAL_CLASSES: Final[tuple[str, ...]] = (
    "script",
    "workflow",
    "cron_or_unit",
    "credential",
    "permission",
    "documentation",
    "configuration_flag",
)

CONTROLLER_CYCLES: Final[tuple[str, ...]] = ("deploy", "redeploy")

# ── DisplacementWindow.v1 ───────────────────────────────────────────────────
#
# `controller_receipts` prove two POSITIVE cycles. Nothing in v1 proved the
# negative, and the negative is the harder half: a window in which the legacy
# executor was not invoked reads as displacement, but two readings collapse
# into it —
#
#   (a) the executor is idle and the controller owns the runtime; or
#   (b) the runtime changed and NEITHER executor did it.
#
# (b) is not a legacy invocation, so a zero-invocation guard passes while the
# controller's claim to own the runtime is false. There is a third executor and
# the measurement cannot see it.
#
# The repair is the inversion `family_absence` already makes for hosts:
# ATTRIBUTION, NOT ABSENCE. The window does not assert that nothing happened;
# it enumerates everything that happened and attributes each one. A change with
# no attribution IS the finding.

#: How the window's events were obtained. Only sources that observe every
#: transition qualify.
EVENT_SOURCE_METHODS: Final[dict[str, str]] = {
    "event_stream": "a subscription delivering every transition as it occurs",
    "audit_log": "an append-only record the runtime writes on every change",
    "daemon_event_api": "the container daemon's own event feed",
}

#: Named so the refusal can say WHY, rather than failing an unknown value. Each
#: of these samples state and infers the gaps, so a change that begins and ends
#: between two samples leaves no trace — which is precisely the third-executor
#: shape the window exists to catch.
EVENT_SOURCE_METHODS_REFUSED: Final[dict[str, str]] = {
    "poll": "samples state; a change between two samples leaves no trace",
    "periodic_snapshot": "same defect on a longer interval",
    "quiet_window": (
        "observes the ABSENCE of invocations, which is the claim under test "
        "rather than evidence for it"
    ),
}

#: `complete` is a claim about the SOURCE, and the chain check below is what
#: tests it. `cannot_establish` is not a caveat on a pass — it forces the
#: verdict to `unmonitored`, because ADR-0018's rule applies to this contract
#: too: an unmonitored region is honestly unmonitored, never quietly exempt.
EVENT_SOURCE_COMPLETENESS: Final[tuple[str, ...]] = ("complete", "cannot_establish")

WINDOW_VERDICTS: Final[dict[str, str]] = {
    "displaced": "every runtime change in the window is attributed",
    "unmonitored": "the source cannot prove it saw every change",
    "not_displaced": "a change was attributed to the legacy executor",
}

#: A runtime change that is NOT a deployment. Every one of them is
#: same-image BY DEFINITION, and that is checked rather than trusted: a change
#: whose image identity moved is a deployment, whatever it is labelled. This is
#: where Docker's benign case lives — `restart: unless-stopped` bringing back
#: the same container is `same_container_restart`, and the schema proves the
#: sameness instead of accepting the word.
NON_DEPLOYMENT_CAUSES: Final[dict[str, str]] = {
    "same_container_restart": "the supervisor restarted the same container",
    "host_reboot_same_image": "the host rebooted and the same image came back",
    "daemon_restart_same_image": "the container daemon restarted",
    "operator_stop_start_same_image": "a person cycled the service, deploying nothing",
}

WINDOW_KEYS: Final[frozenset[str]] = frozenset(
    {
        "from",
        "to",
        "start_runtime",
        "end_runtime",
        "event_source",
        "verdict",
        "runtime_changes",
    }
)

# ── The no-autonomous-return proof ──────────────────────────────────────────
#
# What the `runtime_reactivation` family reframes. A retirement used to owe
# "the artifact is gone". It now owes "the executor CANNOT COME BACK", and
# those are different claims — a script deleted from a tree whose unit is still
# enabled, or whose image is still named by a `restart: always` service, comes
# back at the next reboot with nobody having invoked anything.

RETURN_PROOF_METHODS: Final[dict[str, str]] = {
    "observed_reboot": (
        "the host was rebooted and the subject did not return — the only "
        "method that tests the property directly"
    ),
    "supervisor_catalog": (
        "every reactivation mechanism was inspected and shown incapable; "
        "requires the mechanisms to be enumerated, because an inspection of an "
        "unstated set proves nothing"
    ),
    "mechanism_absent": "no reactivation mechanism existed to begin with",
}

MECHANISM_DISPOSITIONS: Final[tuple[str, ...]] = (
    "removed",
    "disabled_and_verified",
    "never_applied",
)


class ReceiptError(ValueError):
    """The receipt is not admissible. Never a warning."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ReceiptError(message)


def _coordinate(row: Any, where: str, keys: tuple[str, ...]) -> None:
    _require(isinstance(row, dict), f"{where}: must be a table")
    for key in keys:
        value = row.get(key)
        _require(
            isinstance(value, str) and bool(value),
            f"{where}: `{key}` is required. A coordinate a later reader cannot "
            "re-resolve is a summary, not a measurement",
        )


def validate_receipt(
    text: str, *, source: str, inventory: Inventory | None = None
) -> dict:
    """Parse and refuse. Returns the receipt when every refusal passes.

    The refusals, and what each one is for:

    * **an unknown schema or key** — a receipt whose reader and writer disagree
      about the vocabulary proves nothing.
    * **no status, or an unknown one** — absence is not a status. A receipt
      that forgot to say whether the removal happened reads as if it did.
    * **fewer than two controller receipts, or two that are the same run** —
      "we deployed twice" is a sentence; two distinct runs with distinct head
      commits is a measurement. One successful deployment proves the
      replacement can deploy; only the second proves it can deploy AGAIN, over
      its own previous state, which is the property the legacy executor had.
    * **a subject that is not `displaced` in the inventory** — a replacement is
      not adopted while the displaced executor can still act normally, so
      `active_executor` may not be receipted straight to `retired`.
    * **a removal class the subject has and the receipt omits** — a credential
      left live is a second executor waiting for someone with the key.
    * **a zero-surface guard with no sensitivity proof** — a guard nobody
      proved fires is a guard that passes over an empty set (ADR-0018 §5).
    * **a recovery verdict with no exercise coordinate** — "rollback is
      documented" is not "rollback was performed and observed". This is the
      field that stands between a retirement and an outage.
    * **a value-shaped string anywhere** — identity names and digests only.
    * **a committed receipt whose digest does not match its content** — an
      immutable record that can be edited is a mutable record with a stern
      comment.
    """
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ReceiptError(f"{source}: not readable as TOML: {exc}") from exc

    unknown = sorted(set(raw) - RECEIPT_KEYS)
    _require(not unknown, f"{source}: unknown key(s) {unknown}")
    _require(
        raw.get("schema") == RECEIPT_SCHEMA,
        f"{source}: schema must be {RECEIPT_SCHEMA!r}; found {raw.get('schema')!r}",
    )
    status = raw.get("status")
    _require(
        isinstance(status, str) and status in RECEIPT_STATUSES,
        f"{source}: `status` is required and must be one of "
        f"{sorted(RECEIPT_STATUSES)}. Absence is not a status",
    )
    for key in ("receipt_id", "product"):
        _require(
            isinstance(raw.get(key), str) and bool(raw[key]),
            f"{source}: `{key}` is required",
        )
    if status == "superseded":
        _require(
            isinstance(raw.get("superseded_by"), str) and bool(raw["superseded_by"]),
            f"{source}: a superseded receipt names the receipt that replaced "
            "it; corrections are by supersession, never by edit",
        )

    # ── 1. Subject ──────────────────────────────────────────────────────────
    subject = raw.get("subject")
    _coordinate(
        subject,
        f"{source}: subject",
        ("entrypoint", "family", "inventory_digest"),
    )
    _require(
        subject["family"] in FAMILY_BY_NAME,
        f"{source}: subject family {subject['family']!r} is not an entry-point "
        f"family; the families are {list(FAMILY_NAMES)}",
    )

    # ── 2. Two controller receipts, and they must be different runs ─────────
    cycles = raw.get("controller_receipts")
    _require(
        isinstance(cycles, list), f"{source}: `controller_receipts` must be a list"
    )
    _require(
        len(cycles) >= 2,
        f"{source}: {len(cycles)} controller receipt(s). TWO successful "
        "controller-owned cycles are required — deploy and redeploy. One proves "
        "the replacement can deploy; only the second proves it can deploy again "
        "over its own previous state",
    )
    seen_cycles: set[str] = set()
    run_ids: set[str] = set()
    head_commits: set[str] = set()
    for index, cycle in enumerate(cycles):
        where = f"{source}: controller_receipts[{index}]"
        _coordinate(
            cycle,
            where,
            ("cycle", "run_id", "head_commit", "observed_at", "observed_by", "outcome"),
        )
        _require(
            cycle["cycle"] in CONTROLLER_CYCLES,
            f"{where}: `cycle` must be one of {list(CONTROLLER_CYCLES)}",
        )
        _require(
            cycle["outcome"] == "success",
            f"{where}: only a SUCCESSFUL controller cycle counts; found "
            f"{cycle['outcome']!r}",
        )
        _require(
            _is_commit(cycle["head_commit"]),
            f"{where}: `head_commit` must be a 40-character commit",
        )
        seen_cycles.add(cycle["cycle"])
        run_ids.add(cycle["run_id"])
        head_commits.add(cycle["head_commit"])
    _require(
        seen_cycles == set(CONTROLLER_CYCLES),
        f"{source}: both cycles are required; found {sorted(seen_cycles)}",
    )
    _require(
        len(run_ids) >= 2,
        f"{source}: the two controller receipts name the same run. One run "
        "cited twice is one deployment, however it is labelled",
    )

    # ── 3. Removals, by class ───────────────────────────────────────────────
    removals = raw.get("removals")
    _require(isinstance(removals, list), f"{source}: `removals` must be a list")
    _require(
        bool(removals),
        f"{source}: a retirement with no removals is a status change. The "
        "scoreboard counts removals",
    )
    removed_classes: set[str] = set()
    for index, removal in enumerate(removals):
        where = f"{source}: removals[{index}]"
        _coordinate(removal, where, ("class", "identity", "removed_in"))
        _require(
            removal["class"] in REMOVAL_CLASSES,
            f"{where}: unknown removal class {removal['class']!r}; the classes "
            f"are {list(REMOVAL_CLASSES)}",
        )
        _require(
            _is_commit(removal["removed_in"]),
            f"{where}: `removed_in` must be the 40-character commit that "
            "removed it. A pull-request number moves; a commit does not",
        )
        _require(
            not secret_shaped(removal["identity"]),
            f"{where}: `identity` holds a value-shaped string. A credential is "
            "NAMED in a receipt, never held",
        )
        removed_classes.add(removal["class"])

    if inventory is not None:
        matching = [
            entry
            for entry in inventory.entrypoints
            if entry.name == subject["entrypoint"]
        ]
        _require(
            bool(matching),
            f"{source}: subject {subject['entrypoint']!r} is not in "
            f"{inventory.product}'s inventory. A receipt for an entrypoint "
            "nobody declared cannot be reconciled with anything",
        )
        entry = matching[0]
        _require(
            entry.disposition in ("displaced", "retired", "retained_rollback"),
            f"{source}: subject {entry.name!r} is `{entry.disposition}`. A "
            "replacement is not adopted while the displaced executor can still "
            "act normally, so a retirement receipt is admissible only after "
            "`displaced`",
        )
        _require(
            subject["inventory_digest"] == inventory_digest(inventory),
            f"{source}: the subject's inventory digest does not match the "
            "inventory it names. The receipt describes a census that has since "
            "changed",
        )
        if entry.credential and entry.credential != CREDENTIAL_NONE:
            _require(
                "credential" in removed_classes,
                f"{source}: {entry.name} holds credential "
                f"{entry.credential!r} and the receipt removes no credential. "
                "A live credential is a second executor waiting for whoever "
                "holds it",
            )

    # ── 4. The zero-surface guard, with its sensitivity proof ───────────────
    guard = raw.get("zero_surface_guard")
    _coordinate(
        guard,
        f"{source}: zero_surface_guard",
        ("family", "check", "sensitivity_proof"),
    )
    _require(
        guard["family"] == subject["family"],
        f"{source}: the guard covers family {guard['family']!r} but the subject "
        f"is in {subject['family']!r}. A guard over the wrong family is not "
        "coverage",
    )

    # ── 4b. The displacement window, and the two cycles inside it ───────────
    attributed_runs = _validate_window(raw, source, status)
    if attributed_runs or raw.get("displacement_window") is not None:
        outside = sorted(run_ids - attributed_runs)
        _require(
            not outside or status != "committed",
            f"{source}: controller run(s) {outside} are cited as proof but do "
            "not appear as attributed changes inside the displacement window. "
            "A cycle the window did not see is a cycle the window cannot "
            "vouch for",
        )

    # ── 4c. The subject cannot come back on its own ─────────────────────────
    _validate_no_return(raw, source, status, inventory)

    # ── 5. The PROVED recovery verdict ──────────────────────────────────────
    recovery = raw.get("recovery_verdict")
    _coordinate(
        recovery,
        f"{source}: recovery_verdict",
        ("verdict", "restored_from", "exercise_run_id", "observed_at", "observed_by"),
    )
    _require(
        recovery["verdict"] in ("recovered", "not_recovered"),
        f"{source}: `verdict` must be `recovered` or `not_recovered`. "
        '"documented", "expected" and "believed" are not verdicts',
    )
    _require(
        recovery["verdict"] == "recovered" or status != "committed",
        f"{source}: a committed retirement requires a PROVED recovery. A "
        "`not_recovered` verdict is exactly the state in which deleting the "
        "legacy executor removes the rollback path",
    )

    # ── 6. What was deliberately retained ───────────────────────────────────
    retained = raw.get("retained_rollback", [])
    _require(
        isinstance(retained, list), f"{source}: `retained_rollback` must be a list"
    )
    by_name = (
        {entry.name: entry for entry in inventory.entrypoints}
        if inventory is not None
        else {}
    )
    for index, item in enumerate(retained):
        _coordinate(item, f"{source}: retained_rollback[{index}]", ("identity", "why"))
        # The retention this receipt CREATES is the one that outlives it. A key
        # retained here is reachable for as long as the rollback path is, so its
        # constraints are the retirement's business rather than a later audit's.
        held = by_name.get(item["identity"])
        if held is not None:
            failures = rollback_key_failures(held)
            _require(
                not failures,
                f"{source}: retained_rollback[{index}] — " + "; ".join(failures),
            )

    # ── 7. The digest over the receipt's own content ────────────────────────
    computed = receipt_digest(raw)
    if status == "committed":
        _require(
            raw.get("digest") == computed,
            f"{source}: `digest` does not match this receipt's content "
            f"(expected {computed}). An immutable record that can be edited is "
            "a mutable record with a stern comment",
        )
    return raw


def _validate_window(raw: dict, source: str, status: str) -> set[str]:
    """`DisplacementWindow.v1`. Returns the controller run ids it attributes.

    Bounded, chained, and complete or honestly unmonitored. The chain is the
    part that does the real work: consecutive changes must meet
    (`runtime_after` == the next `runtime_before`) and the declared endpoints
    must match the ends. A source that missed a change leaves a BREAK in the
    chain, so completeness is tested rather than declared — which is what
    "complete, named event source" has to mean if it is to mean anything.
    """
    window = raw.get("displacement_window")
    if window is None:
        _require(
            status != "committed",
            f"{source}: a committed retirement requires a "
            "`displacement_window`. Two successful controller cycles prove the "
            "replacement WORKS; only the window proves the legacy executor "
            "stopped acting, and a quiet interval is not that proof",
        )
        return set()

    _require(
        isinstance(window, dict), f"{source}: `displacement_window` must be a table"
    )
    unknown = sorted(set(window) - WINDOW_KEYS)
    _require(not unknown, f"{source}: displacement_window unknown key(s) {unknown}")
    _coordinate(
        window,
        f"{source}: displacement_window",
        ("from", "to", "start_runtime", "end_runtime", "verdict"),
    )
    _require(
        window["verdict"] in WINDOW_VERDICTS,
        f"{source}: displacement_window verdict must be one of "
        f"{sorted(WINDOW_VERDICTS)}",
    )
    _require(
        window["from"] < window["to"],
        f"{source}: displacement_window is not bounded forwards "
        f"({window['from']} -> {window['to']}). An open or inverted window is "
        "not an observation",
    )

    source_row = window.get("event_source")
    _coordinate(
        source_row,
        f"{source}: displacement_window.event_source",
        ("name", "method", "completeness"),
    )
    method = source_row["method"]
    _require(
        method not in EVENT_SOURCE_METHODS_REFUSED,
        f"{source}: event source method {method!r} is refused — "
        f"{EVENT_SOURCE_METHODS_REFUSED.get(method, '')}",
    )
    _require(
        method in EVENT_SOURCE_METHODS,
        f"{source}: unknown event source method {method!r}; the methods that "
        f"observe every transition are {sorted(EVENT_SOURCE_METHODS)}",
    )
    completeness = source_row["completeness"]
    _require(
        completeness in EVENT_SOURCE_COMPLETENESS,
        f"{source}: event source completeness must be one of "
        f"{list(EVENT_SOURCE_COMPLETENESS)}",
    )
    if completeness == "cannot_establish":
        _require(
            window["verdict"] == "unmonitored",
            f"{source}: event source {source_row['name']!r} cannot establish "
            "completeness, so the verdict is UNMONITORED. A pass with a caveat "
            "is the shape ADR-0018 exists to refuse, and this contract does not "
            "get an exemption from its own rule",
        )
    _require(
        window["verdict"] == "displaced" or status != "committed",
        f"{source}: a committed retirement requires a `displaced` window "
        f"verdict; found {window['verdict']!r}",
    )

    changes = window.get("runtime_changes")
    _require(
        isinstance(changes, list),
        f"{source}: displacement_window.runtime_changes must be a list",
    )

    attributed_runs: set[str] = set()
    ordered: list[dict] = []
    for index, change in enumerate(changes):
        where = f"{source}: displacement_window.runtime_changes[{index}]"
        _coordinate(change, where, ("observed_at", "runtime_before", "runtime_after"))
        unknown_change = sorted(
            set(change)
            - {
                "observed_at",
                "runtime_before",
                "runtime_after",
                "controller_receipt",
                "non_deployment_cause",
                "note",
            }
        )
        _require(not unknown_change, f"{where}: unknown key(s) {unknown_change}")

        run = change.get("controller_receipt")
        cause = change.get("non_deployment_cause")
        _require(
            bool(run) or bool(cause),
            f"{where}: UNATTRIBUTED. Every runtime change is linked to a "
            "controller receipt or to a typed non-deployment cause. A change "
            "nobody can account for is a third executor, and this is the field "
            "that finds it",
        )
        _require(
            not (run and cause),
            f"{where}: attributed to a controller receipt AND a "
            "non-deployment cause. One change has one cause; an ambiguous "
            "attribution is an unattributed one with two labels",
        )
        if cause:
            _require(
                cause in NON_DEPLOYMENT_CAUSES,
                f"{where}: unknown non-deployment cause {cause!r}; the causes "
                f"are {sorted(NON_DEPLOYMENT_CAUSES)}",
            )
            _require(
                change["runtime_before"] == change["runtime_after"],
                f"{where}: attributed to {cause!r}, but the runtime identity "
                f"moved ({change['runtime_before']} -> "
                f"{change['runtime_after']}). A change that altered what is "
                "running IS a deployment, whatever it is called — this is "
                "exactly the distinction a restart policy blurs",
            )
        else:
            attributed_runs.add(run)
        ordered.append(change)

    ordered.sort(key=lambda row: row["observed_at"])
    if ordered:
        _require(
            window["start_runtime"] == ordered[0]["runtime_before"],
            f"{source}: the window opens at {window['start_runtime']} but its "
            f"first change starts from {ordered[0]['runtime_before']}. The gap "
            "is a change nobody recorded",
        )
        _require(
            window["end_runtime"] == ordered[-1]["runtime_after"],
            f"{source}: the window closes at {window['end_runtime']} but its "
            f"last change ends at {ordered[-1]['runtime_after']}. The gap is a "
            "change nobody recorded",
        )
        for first, second in itertools.pairwise(ordered):
            _require(
                first["runtime_after"] == second["runtime_before"],
                f"{source}: the change chain BREAKS between "
                f"{first['observed_at']} and {second['observed_at']} "
                f"({first['runtime_after']} != {second['runtime_before']}). A "
                "break is a transition the source did not see, which is the "
                "completeness claim failing rather than the window being quiet",
            )
    else:
        _require(
            window["start_runtime"] == window["end_runtime"],
            f"{source}: the window records no change, yet the runtime moved "
            f"({window['start_runtime']} -> {window['end_runtime']}). That "
            "movement is the third executor",
        )
    return attributed_runs


def _validate_no_return(raw: dict, source: str, status: str, inventory) -> None:
    """The subject cannot come back on its own.

    A retirement used to owe "the artifact is gone". Since the
    `runtime_reactivation` family exists it owes more: a script deleted from a
    tree whose unit is still enabled, or whose image is still named by a
    `restart: always` service, returns at the next reboot with nobody having
    invoked anything. That is not a retirement; it is a pause.
    """
    proof = raw.get("no_autonomous_return")
    if proof is None:
        _require(
            status != "committed",
            f"{source}: a committed retirement requires a "
            "`no_autonomous_return` proof. Removing the artifact and proving "
            "it cannot return are different claims, and only the second "
            "survives a reboot",
        )
        return

    _coordinate(
        proof,
        f"{source}: no_autonomous_return",
        ("method", "observed_at", "observed_by", "host"),
    )
    method = proof["method"]
    _require(
        method in RETURN_PROOF_METHODS,
        f"{source}: unknown no_autonomous_return method {method!r}; the "
        f"methods are {sorted(RETURN_PROOF_METHODS)}",
    )

    mechanisms = proof.get("mechanisms", [])
    _require(
        isinstance(mechanisms, list),
        f"{source}: no_autonomous_return.mechanisms must be a list",
    )
    accounted: set[str] = set()
    for index, row in enumerate(mechanisms):
        where = f"{source}: no_autonomous_return.mechanisms[{index}]"
        _coordinate(row, where, ("identity", "disposition_after"))
        _require(
            row["disposition_after"] in MECHANISM_DISPOSITIONS,
            f"{where}: disposition_after must be one of "
            f"{list(MECHANISM_DISPOSITIONS)}",
        )
        accounted.add(row["identity"])

    _require(
        method != "supervisor_catalog" or bool(mechanisms),
        f"{source}: `supervisor_catalog` inspected an unstated set. An "
        "inspection that does not say WHAT it inspected proves nothing",
    )

    if inventory is not None:
        subject = raw["subject"]["entrypoint"]
        naming = sorted(
            entry.name
            for entry in inventory.entrypoints
            if subject in entry.reactivates
        )
        missing = sorted(set(naming) - accounted)
        _require(
            not missing,
            f"{source}: {missing} can reactivate {subject!r} and the receipt "
            "does not account for them. This is the ordering the transition "
            "table deliberately does not enforce: a displaced executor with a "
            "live resurrection path was never displaced",
        )


def receipt_digest(receipt: dict) -> str:
    """The digest over everything the receipt asserts, excluding the digest."""
    payload = {key: value for key, value in receipt.items() if key != "digest"}
    return canonical_digest(payload)


# ── CLI ─────────────────────────────────────────────────────────────────────


def load_baseline() -> dict:
    if not BASELINE_PATH.is_file():
        return {"products": {}}
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("--validate-receipt", default=None)
    parser.add_argument("--inventory-dir", default=None)
    args = parser.parse_args(argv)

    directory = (
        pathlib.Path(args.inventory_dir) if args.inventory_dir else INVENTORY_DIR
    )

    if args.validate_receipt:
        path = pathlib.Path(args.validate_receipt)
        product_inventory: Inventory | None = None
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
            candidate = directory / f"{raw.get('product')}.toml"
            if candidate.is_file():
                product_inventory = parse_inventory(
                    candidate.read_text(encoding="utf-8"), source=candidate.name
                )
        except (OSError, tomllib.TOMLDecodeError, InventoryError):
            product_inventory = None
        try:
            validate_receipt(
                path.read_text(encoding="utf-8"),
                source=path.name,
                inventory=product_inventory,
            )
        except (ReceiptError, OSError) as exc:
            print(f"REFUSED {exc}")
            return 1
        print(f"admissible: {path.name}")
        return 0

    baseline = load_baseline()
    measured, unadopted, unverified = measure(baseline, directory)
    print(coverage(measured, unadopted, unverified))

    if args.write_baseline:
        BASELINE_PATH.write_text(
            json.dumps(build_baseline(measured, baseline), indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        print(f"wrote {BASELINE_PATH.relative_to(PROJECT_ROOT)}")
        return 0

    failures, abstentions = ratchet(measured, baseline)
    failures.extend(ratchet_adoption(measured, unadopted, baseline))
    for line in abstentions:
        print(f"ABSTAIN {line}")
    if failures:
        print("\nexecutor-retirement ratchet:")
        for line in failures:
            print(f"  {line}")
        return 1 if args.check else 0
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    sys.exit(main())
