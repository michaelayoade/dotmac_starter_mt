"""The hardened OCI image contract, and an audit that proves it against bytes.

Ported from `dotmac_integrator:scripts/audit_image.sh:28-85`, which is the only
test any product in the fleet has for deployment behaviour, and which carries
its own sensitivity proof (that repository's rule 17). The port keeps the three
assertions it makes and adds the rest of the contract; the sensitivity proof is
a parity obligation this package inherits rather than a nicety.

## Why the audit reads JSON rather than running Docker

`audit_image.sh` shells out to `docker inspect` and `docker history`. That makes
it real, and it makes it untestable anywhere without a daemon and a built
image — so the guard itself is never exercised, and a guard nobody exercises is
a guard that has silently stopped biting. Here the audit is a pure function
over the JSON those commands ALREADY produce: `audit_image(config, history)`.
The CLI shells out and hands the parsed result in; a test hands in a dictionary.

That is not a weaker check. It is the same check, on the same bytes, that can
also be shown to fail — which under ADR-0018 is the difference between a guard
and a decoration.

## What the contract requires, and the failure each requirement prevents

Every rule below is one of the eighteen defects the inventory recorded, turned
into something a machine refuses.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

# Tooling that has no business in a runtime image. Presence of any of these
# means the multi-stage build leaked its builder, and "we deleted it in a later
# layer" does not help: every layer is retrievable.
FORBIDDEN_RUNTIME_TOOLS: Final[tuple[str, ...]] = (
    "pytest",
    "poetry",
    "pip-tools",
    "mypy",
    "ruff",
    "bandit",
    "gcc",
    "g++",
    "make",
    "git",
    "npm",
    "node",
    "curl-config",
)

# Paths that must not exist in a runtime image, because each one is either the
# build context, the source of truth for a build, or a credential store.
FORBIDDEN_RUNTIME_PATHS: Final[tuple[str, ...]] = (
    "/app/.git",
    "/app/tests",
    "/app/.env",
    "/root/.cache/pip",
    "/root/.config/pypoetry",
    "/app/poetry.lock",
    "/app/.netrc",
    "/root/.netrc",
)

# Anything in image history that looks like a credential having been passed as
# a build argument rather than a BuildKit secret. A `--build-arg TOKEN=…` is
# recorded in the image's own history and is readable by anyone who can pull it.
_HISTORY_SECRET = re.compile(
    r"(PASSWORD|TOKEN|SECRET|API[_-]?KEY|CREDENTIAL)\s*=\s*[^\s\"']{4,}",
    re.IGNORECASE,
)

_MIGRATION_MARKERS: Final[tuple[str, ...]] = (
    "alembic",
    "upgrade head",
    "upgrade heads",
    "migrate",
    "manage.py migrate",
)

_REQUIRED_LABELS: Final[tuple[str, ...]] = (
    "org.opencontainers.image.revision",
    "org.opencontainers.image.source",
    "org.opencontainers.image.version",
)


@dataclass(frozen=True, slots=True)
class Finding:
    """One contract violation, with the rule that caught it."""

    rule: str
    detail: str

    def __str__(self) -> str:
        return f"{self.rule}: {self.detail}"


@dataclass(frozen=True, slots=True)
class AuditReport:
    reference: str
    findings: tuple[Finding, ...] = field(default_factory=tuple)

    @property
    def passed(self) -> bool:
        return not self.findings

    def render(self) -> str:
        if self.passed:
            return f"{self.reference}: image contract satisfied ({len(RULES)} rules)\n"
        lines = [f"{self.reference}: {len(self.findings)} image-contract violation(s)"]
        lines.extend(f"  - {finding}" for finding in self.findings)
        return "\n".join(lines) + "\n"


# ── individual rules, each a pure predicate over the inspect config ──────────


def _config(inspect: Mapping[str, Any]) -> Mapping[str, Any]:
    """The `Config` block, tolerating both `docker inspect` shapes.

    `docker image inspect` returns a list; `docker inspect --format '{{json
    .Config}}'` returns the block directly. Accepting both is not laxness — it
    is the difference between an audit that works and an audit whose failure
    mode is "the operator used the other invocation".
    """
    if "Config" in inspect and isinstance(inspect["Config"], Mapping):
        block: Mapping[str, Any] = inspect["Config"]
        return block
    return inspect


def _entry(inspect: Mapping[str, Any]) -> list[str]:
    config = _config(inspect)
    parts: list[str] = []
    for key in ("Entrypoint", "Cmd"):
        value = config.get(key)
        if isinstance(value, list):
            parts.extend(str(item) for item in value)
        elif isinstance(value, str):
            parts.append(value)
    return parts


def rule_non_root(inspect: Mapping[str, Any]) -> list[Finding]:
    """The image declares a fixed non-root UID/GID.

    An image with no `USER` runs as uid 0. `read_only` and `cap_drop` at the
    Compose layer mitigate that, but they are the *deployment's* choice, and an
    image that only behaves when deployed carefully is an image that will
    eventually be deployed carelessly.

    The UID is required to be NUMERIC and non-zero. The reusable workflow may
    override the user to uid/gid 0 for its one-shot filesystem INSPECTION; that
    does not weaken this rule, because the rule reads the image's configured
    ``Config.User`` from inspect rather than the collector process's identity.
    A named user is resolved
    against the image's own `/etc/passwd`, so a `USER app` whose entry was
    dropped in a later layer silently becomes root again, and a numeric uid also
    lets a host map volume ownership without introspecting the image.
    """
    user = str(_config(inspect).get("User", "")).strip()
    if not user:
        return [
            Finding(
                "non-root",
                "the image declares no USER, so it runs as uid 0. Add a fixed "
                "numeric UID:GID (10001:10001 is the fleet convention)",
            )
        ]
    uid = user.split(":", 1)[0]
    if not uid.isdigit():
        return [
            Finding(
                "non-root",
                f"USER is {user!r}, a NAME rather than a numeric uid. A name is "
                "resolved against the image's own /etc/passwd, so an entry lost "
                "in a later layer silently restores root",
            )
        ]
    if int(uid) == 0:
        return [Finding("non-root", f"USER is {user!r}, which is root")]
    if int(uid) < 1000:
        return [
            Finding(
                "non-root",
                f"USER uid {uid} is in the system range (<1000) and may collide "
                "with a distribution account the base image adds later",
            )
        ]
    return []


def rule_no_migration_on_boot(inspect: Mapping[str, Any]) -> list[Finding]:
    """The default command does not migrate.

    `AGENTS.md` rule 13 in the Starter and rule 5 in the Integrator both say
    migrations run at deploy time as the owner role, never on container boot.
    An image whose `CMD` migrates makes that unenforceable: every replica races
    to migrate on every restart, using whatever credential the runtime role
    happens to hold — which is the credential that is supposed to be unable to
    do it.
    """
    joined = " ".join(_entry(inspect)).lower()
    hits = [marker for marker in _MIGRATION_MARKERS if marker in joined]
    if hits:
        return [
            Finding(
                "no-migration-on-boot",
                f"the default command contains {hits}. Migrations run once, at "
                "deploy time, as the owner role — not on every container start "
                "by every replica",
            )
        ]
    return []


def rule_no_build_tooling(
    inspect: Mapping[str, Any], *, layers: Sequence[str] = ()
) -> list[Finding]:
    """No test or build tooling in the runtime image.

    Checked against the recorded filesystem entries when they are available. An
    empty `layers` means the check could not run, and that is reported as a
    finding rather than a pass — an audit that silently skips its own rule is
    worse than no audit, because it reports green.
    """
    if not layers:
        return [
            Finding(
                "no-build-tooling",
                "no filesystem listing was supplied, so this rule could not be "
                "evaluated. It is reported as a violation rather than skipped: a "
                "check that cannot run has not passed",
            )
        ]
    found = sorted(
        {
            tool
            for tool in FORBIDDEN_RUNTIME_TOOLS
            for entry in layers
            if entry.endswith(f"/{tool}") or entry.endswith(f"/{tool}.exe")
        }
    )
    findings = (
        [
            Finding(
                "no-build-tooling",
                f"runtime image contains build/test tooling: {found}",
            )
        ]
        if found
        else []
    )
    # Prefix match, not equality. A filesystem listing enumerates FILES, so
    # `/app/.git` never appears as an entry while `/app/.git/config` does — an
    # exact-match check finds a build context only when the directory itself
    # happens to be listed, which is to say almost never.
    entries = tuple(layers)
    present = sorted(
        {
            path
            for path in FORBIDDEN_RUNTIME_PATHS
            if any(entry == path or entry.startswith(path + "/") for entry in entries)
        }
    )
    if present:
        findings.append(
            Finding(
                "no-build-context",
                f"runtime image contains build-context or credential paths: {present}",
            )
        )
    return findings


def rule_no_secret_in_history(
    inspect: Mapping[str, Any], *, history: Sequence[str] = ()
) -> list[Finding]:
    """No credential was passed as a build argument.

    A `--build-arg FORGEJO_PASSWORD=…` is recorded in the image's own history
    and readable by anyone who can pull the image. The correct mechanism is a
    BuildKit secret mount, which leaves no trace, and both `dotmac_sub` and
    `dotmac_integrator` already use one — this rule is what keeps the next
    Dockerfile from taking the easy route.
    """
    if not history:
        return [
            Finding(
                "no-secret-in-history",
                "no image history was supplied, so this rule could not be "
                "evaluated and is reported as a violation rather than skipped",
            )
        ]
    findings: list[Finding] = []
    for entry in history:
        match = _HISTORY_SECRET.search(entry)
        if match is None:
            continue
        # Report the NAME and never the value: the audit's output goes to CI
        # logs, and echoing a leaked credential into a second place is not a
        # fix.
        name = match.group(1)
        findings.append(
            Finding(
                "no-secret-in-history",
                f"image history assigns {name}=… — use a BuildKit secret mount; "
                "a build argument is permanently readable by anyone who can pull "
                "the image, and this credential must now be rotated",
            )
        )
    return findings


def rule_required_labels(inspect: Mapping[str, Any]) -> list[Finding]:
    """The image says which commit it came from.

    Without `org.opencontainers.image.revision` nothing connects the running
    bytes to a reviewable tree, and the deployment engine's revision gate has
    nothing to check. `dotmac_sub` additionally requires the label to be 40 hex
    characters, because a short SHA is ambiguous across a large history.
    """
    labels = _config(inspect).get("Labels") or {}
    if not isinstance(labels, Mapping):
        return [Finding("required-labels", "Labels is not a mapping")]
    findings: list[Finding] = []
    for label in _REQUIRED_LABELS:
        if not str(labels.get(label, "")).strip():
            findings.append(Finding("required-labels", f"missing label {label}"))
    revision = str(labels.get("org.opencontainers.image.revision", ""))
    if revision and not re.fullmatch(r"[0-9a-f]{40}", revision):
        findings.append(
            Finding(
                "required-labels",
                f"org.opencontainers.image.revision is {revision!r}; it must be a "
                "full 40-character commit SHA, because a short SHA is ambiguous "
                "and a branch name is not a coordinate at all",
            )
        )
    return findings


def rule_no_shell_form_entrypoint(inspect: Mapping[str, Any]) -> list[Finding]:
    """PID 1 receives signals.

    A shell-form `CMD uvicorn …` becomes `/bin/sh -c "uvicorn …"`, and `sh` does
    not forward SIGTERM to its child. The container then ignores every graceful
    stop and is SIGKILLed when the grace period expires — in-flight requests
    dropped, connections severed, and a `stop_grace_period` that does nothing
    while appearing to be configured.
    """
    config = _config(inspect)
    for key in ("Entrypoint", "Cmd"):
        value = config.get(key)
        if not isinstance(value, list) or len(value) < 3:
            continue
        if value[0] in ("/bin/sh", "/bin/bash", "sh", "bash") and value[1] == "-c":
            return [
                Finding(
                    "exec-form-entrypoint",
                    f"{key} is shell form ({value[0]} -c …), so PID 1 is a shell "
                    "that does not forward SIGTERM. Use exec form: "
                    '["uvicorn", "app.main:app", …]',
                )
            ]
    return []


def rule_declares_no_ports_it_does_not_serve(
    inspect: Mapping[str, Any],
) -> list[Finding]:
    """`EXPOSE` is documentation, and wrong documentation is worse than none.

    Not a security control — `EXPOSE` publishes nothing. It is checked because
    an operator reads it to decide what to bind, and a stale entry from a
    previous architecture is how a port ends up bound to nothing.
    """
    exposed = _config(inspect).get("ExposedPorts") or {}
    if isinstance(exposed, Mapping) and len(exposed) > 4:
        return [
            Finding(
                "expose-hygiene",
                f"{len(exposed)} exposed ports declared. EXPOSE is read by "
                "operators as a statement of what this image serves; a long list "
                "means it is stale",
            )
        ]
    return []


RULES: Final[tuple[str, ...]] = (
    "non-root",
    "no-migration-on-boot",
    "no-build-tooling",
    "no-build-context",
    "no-secret-in-history",
    "required-labels",
    "exec-form-entrypoint",
    "expose-hygiene",
)


def audit_image(
    reference: str,
    inspect: Mapping[str, Any],
    *,
    history: Sequence[str] = (),
    layers: Sequence[str] = (),
) -> AuditReport:
    """Audit one image against the hardened contract.

    ``inspect`` is what `docker image inspect` produces, ``history`` the
    `CreatedBy` strings from `docker history --no-trunc`, and ``layers`` a
    complete filesystem listing collected as inspection evidence. The
    collector's privilege is deliberately independent of the configured
    runtime user, which this function audits from ``inspect``.

    Nothing here talks to a daemon, which is the point: this function is
    exercised by unit tests with planted violations, so each rule has a
    demonstrated failure and none of them is decoration.
    """
    findings: list[Finding] = []
    findings.extend(rule_non_root(inspect))
    findings.extend(rule_no_migration_on_boot(inspect))
    findings.extend(rule_no_build_tooling(inspect, layers=layers))
    findings.extend(rule_no_secret_in_history(inspect, history=history))
    findings.extend(rule_required_labels(inspect))
    findings.extend(rule_no_shell_form_entrypoint(inspect))
    findings.extend(rule_declares_no_ports_it_does_not_serve(inspect))
    return AuditReport(reference=reference, findings=tuple(findings))


def audit_findings(reports: Iterable[AuditReport]) -> list[Finding]:
    return [finding for report in reports for finding in report.findings]
