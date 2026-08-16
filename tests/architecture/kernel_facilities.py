"""What every kernel public entrypoint is, and what calling it obliges.

`test_facility_prerequisites.py` is the gate; this module is the vocabulary it
reads. Kept separate because the classification is DATA a reviewer argues with,
and a reviewer should not have to read assertions to find it.

## The problem being solved

`dotmac_kernel.idempotency.execute_once` writes `public.idempotency_records` at
REQUEST time. Nothing in a calling module's DDL touches that table, so the
dependency is invisible to every migration gate: the module installs, migrates
cleanly against an adopter running its own lineage, and dies on `UndefinedTable`
at the first guarded call — in production, not at deploy.

Three modules shipped in exactly that state before anything checked
(`dotmac-numbering`, `dotmac-integration`, `dotmac-entitlement-allocation` for
the ledger; `dotmac-approvals` for the relay). The first three were found by
grepping the ledger facility. The fourth was found only because somebody
enumerated the kernel instead of the modules, which is what this file exists to
do permanently.

## Why classification is DERIVED, not listed

`PERSISTENCE_BACKED` is not a hand-written list of interesting functions. The
detector reads the kernel's own source and calls an entrypoint
persistence-backed when it takes a `Session`/`Connection` — the parameter a
caller must supply for the function to reach storage at all. A hand-written list
answers "which facilities did somebody think of?"; the derivation answers "which
facilities are there?", and only the second one fails when the kernel grows.

Every derived entrypoint must then appear in exactly ONE of `MAPPED` or
`FROZEN`. An entrypoint in neither fails the completeness gate by name — that is
the difference between a guard and an allowlist.

Pure helpers are never in either, and are never asked for a prerequisite: a
false requirement on `fingerprint_of` would teach authors that the guard is
noise, which is how a guard stops being read.

## The two obligations, and why both are checked

A module that calls a MAPPED facility must

1. declare the prerequisite on its `ModuleManifest`, so composition can refuse
   an assembly that cannot supply the effect; AND
2. verify it in a migration's requires tuple, so DEPLOY refuses a database that
   does not have it.

Neither implies the other. A manifest declaration with no verifying migration
is a promise nothing checks against a real catalogue; a verifying migration with
no manifest declaration leaves the composed gate blind. Both halves are asserted
separately, and their failure messages say which half is missing.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Final

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
KERNEL_SRC: Final = REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel"
PACKAGES: Final = REPO_ROOT / "packages"

#: Modules whose public callables are NOT module-callable facilities, with the
#: premise that makes each exclusion enforceable rather than convenient
#: (ADR-0018). An exclusion here is a claim a reviewer can check, not a
#: shortening of the list.
NOT_MODULE_FACILITIES: Final[dict[str, str]] = {
    "db": (
        "SUPPLIES the Session rather than consuming one. A module receives a "
        "session from its caller and never builds one; `dotmac_kernel.db` is "
        "the assembly's transaction authority (hard rule 8), and a module "
        "importing it would be creating a second session factory, which a "
        "different gate already refuses."
    ),
    "testing/harness": (
        "Test kit. It builds engines for suites, is never imported by shipped "
        "module code, and an import-linter contract keeps it that way."
    ),
    "testing/fakes": "Test kit; no storage of its own.",
    "testing/provisioning": "Test kit; contract checker, no storage.",
    "testing/licensing": "Test kit; signing fixtures, no storage.",
    "app_factory": (
        "Builds the FastAPI application. An assembly concern: a module that "
        "called it would be assembling a second app inside the first."
    ),
}


@dataclass(frozen=True, slots=True)
class Facility:
    """One kernel entrypoint a module can call, and what it obliges."""

    #: `module.path:symbol`, e.g. `idempotency:execute_once`.
    key: str
    #: The prerequisite a caller must declare, or None when none exists yet.
    prerequisite: str | None
    #: Why, in the reviewer's terms.
    note: str


#: Facilities whose storage HAS a named prerequisite. Calling one obliges the
#: caller to declare it (manifest) and verify it (migration).
#:
#: Aliases are listed as their own entries rather than resolved to a canonical
#: name, because a caller writes the alias and the failure message must name
#: what the caller wrote. `messaging.inbox.process_once` is an adapter over
#: `idempotency.execute_once` (ADR-0014, hard rule 21) and carries the same
#: obligation for the same reason.
MAPPED: Final[tuple[Facility, ...]] = (
    Facility(
        "audit:write_platform_audit_event",
        "platform_audit_log.v1",
        "appends to public.platform_audit_events",
    ),
    Facility(
        "idempotency:execute_once",
        "idempotency_ledger.v1",
        "writes public.idempotency_records",
    ),
    Facility(
        "idempotency:execute_once_platform",
        "idempotency_ledger.v1",
        "writes public.platform_idempotency_records",
    ),
    Facility(
        "idempotency:purge_expired",
        "idempotency_ledger.v1",
        "deletes from the ledger; retention is still the ledger",
    ),
    Facility(
        "messaging/inbox:process_once",
        "idempotency_ledger.v1",
        "adapter over execute_once — the ledger has one owner (hard rule 21)",
    ),
    Facility(
        "messaging/platform:process_once_platform",
        "idempotency_ledger.v1",
        "adapter over execute_once_platform",
    ),
    Facility(
        "messaging/outbox:enqueue_event",
        "outbox_relay.v1",
        "writes public.outbox_events",
    ),
    Facility(
        "messaging/outbox:enqueue_platform_event",
        "outbox_relay.v1",
        "writes public.platform_outbox_events",
    ),
    Facility(
        "messaging/relay:claim_batch",
        "outbox_relay.v1",
        "calls claim_outbox_batch",
    ),
    Facility(
        "messaging/relay:record_success",
        "outbox_relay.v1",
        "calls settle_outbox_event",
    ),
    Facility(
        "messaging/relay:record_failure",
        "outbox_relay.v1",
        "calls settle_outbox_event",
    ),
    Facility(
        "messaging/platform_relay:claim_platform_batch",
        "outbox_relay.v1",
        "calls claim_platform_outbox_batch",
    ),
    Facility(
        "messaging/platform_relay:record_success",
        "outbox_relay.v1",
        "platform peer of relay.record_success",
    ),
    Facility(
        "messaging/platform_relay:record_failure",
        "outbox_relay.v1",
        "platform peer of relay.record_failure",
    ),
)

#: Facilities that touch storage and have NO prerequisite to declare yet.
#:
#: This is not an exemption list. Each entry freezes the EXACT set of module
#: files that call it today, and `test_frozen_facilities_gain_no_new_callers`
#: fails in both directions: a new caller fails because the debt would grow
#: unnoticed, and a departed caller fails because the frozen set has to be
#: lowered deliberately rather than drift (ADR-0018's two-directional ratchet).
#:
#: An empty set means "no module calls this today", which is a claim worth
#: holding: it is how the next caller becomes a visible diff.
FROZEN: Final[dict[str, tuple[str, frozenset[str]]]] = {
    "audit:write_audit_event": (
        "TENANT AUDIT — no published consumer. The persisted-runtime-dependency "
        "inventory found its only module caller is `dotmac-template-studio`, "
        "which has no tag in any version and sits in no release lane, so there "
        "is nothing in the field to protect and no evidence yet for what a "
        "prerequisite would have to verify. Frozen so the first PUBLISHED "
        "consumer is a visible diff rather than a discovery.",
        frozenset(
            {
                "dotmac-template-studio/src/dotmac_template_studio/router.py",
                "dotmac-template-studio/src/dotmac_template_studio/web.py",
            }
        ),
    ),
    "settings_resolver:resolve_value": (
        "SETTINGS — ruled NOT a prerequisite (Michael, 2026-08-16). "
        "`domain_settings` is one table serving both scopes through a nullable "
        "`tenant_id`, the shape ADR-0023's plane gate refuses, and half the "
        "dependency is not DDL at all: the specs are registered in Python by "
        "the assembly, so an adopter with a byte-perfect table still fails on "
        "the first read. A database verifier would certify a deployment that "
        "does not work. Module configuration is a separate architecture "
        "problem — likely a typed assembly-supplied resolver port — and the "
        "ratchet holds the caller set until that exists.",
        frozenset(),
    ),
    "flag_models:resolve_flag": (
        "FLAGS — inventory authorized, not yet run (Michael, 2026-08-16). Same "
        "undeclared-runtime-dependency shape, but its live callers, storage "
        "dependency and plane behaviour have not been measured, and a "
        "prerequisite must not be presumed before that. Frozen meanwhile.",
        frozenset({"dotmac-template-studio/src/dotmac_template_studio/service.py"}),
    ),
    "deps:require_user_auth": (
        "IDENTITY — inventory authorized, not yet run (Michael, 2026-08-16). "
        "Reads `auth_sessions`, `parties` and `party_role_grants`; whether that "
        "is one prerequisite, three, or the wrong instrument entirely is what "
        "the inventory decides.",
        frozenset(),
    ),
    "web_deps:require_web_auth": (
        "IDENTITY — the portal seam over `require_user_auth`; frozen with it "
        "and decided by the same inventory.",
        frozenset(),
    ),
}

#: Facilities that touch storage, have no prerequisite, and are OUT of scope
#: for the module-facility question — because no installable module may call
#: them at all. Kept explicit so the completeness gate can account for every
#: derived entrypoint rather than quietly skipping a subsystem.
#:
#: Each is enforced elsewhere, and the enforcing gate is named: an entry whose
#: premise nothing checks would be a prose exemption.
OUT_OF_SCOPE: Final[dict[str, str]] = {
    "deps:authenticate_request": "identity seam; the auth tier owns it",
    "deps:authorize_party": "identity seam; the auth tier owns it",
    "platform_auth:require_platform_admin": "platform identity seam",
    "external_identity": "kernel-owned identity binding; no module calls it",
    "entitlements": "kernel-owned grants; modules read decisions, not tables",
    "consent": "kernel-owned suppression; delivery reads decisions",
    "delivery": "kernel-owned receipts",
    "delivery_providers": "provider transport, not storage of a module's own",
    "branding": "assembly presentation seam",
    "display": "assembly presentation seam",
    "channel_policy": "kernel-owned policy read",
    "settings_crypto": "settings storage; see the settings ruling in FROZEN",
    "settings_shadow": "settings storage; see the settings ruling in FROZEN",
    "settings_resolver": "settings storage; see the settings ruling in FROZEN",
    "flag_models": "flag storage; see the flags entry in FROZEN",
    "messaging/worker": "the dispatcher process, not a module call path",
    "messaging/platform_worker": "the dispatcher process, not a module call path",
}


def _exported(tree: ast.Module) -> set[str]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", "") == "__all__" for t in node.targets
        ):
            return {
                element.value
                for element in node.value.elts  # type: ignore[attr-defined]
                if isinstance(element, ast.Constant)
            }
    return set()


def _takes_a_session(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Does this entrypoint accept a database handle?

    The discriminator, and deliberately a structural one. A function that
    reaches storage needs a `Session` or `Connection` from its caller — the
    kernel builds neither for a module (`dotmac_kernel.db` is the assembly's,
    see NOT_MODULE_FACILITIES) — so the parameter IS the dependency, visible in
    the signature without reading the body.

    Reading bodies was the alternative and is worse: it drags in every helper a
    function happens to call, and it cannot see through a call into another
    module without becoming a whole-program analysis.
    """
    for argument in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
        if argument.annotation is None:
            continue
        annotation = ast.unparse(argument.annotation)
        if "Session" in annotation or "Connection" in annotation:
            return True
    return False


def derive_persistence_backed() -> dict[str, str]:
    """Every kernel public entrypoint that takes a database handle.

    Returns `{facility key: source file}`. The completeness gate compares this
    against `MAPPED | FROZEN | OUT_OF_SCOPE`, so a kernel release that adds a
    storage-touching public function fails until somebody classifies it.
    """
    found: dict[str, str] = {}
    for path in sorted([*KERNEL_SRC.glob("*.py"), *KERNEL_SRC.glob("*/*.py")]):
        relative = path.relative_to(KERNEL_SRC).with_suffix("").as_posix()
        if relative in NOT_MODULE_FACILITIES or relative.startswith("migrations"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        exported = _exported(tree)
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if node.name.startswith("_") or node.name not in exported:
                continue
            if _takes_a_session(node):
                found[f"{relative}:{node.name}"] = str(path.relative_to(REPO_ROOT))
    return found


def classified() -> dict[str, Facility | None]:
    """Every classified facility: `MAPPED` entries, `FROZEN` as None."""
    result: dict[str, Facility | None] = {f.key: f for f in MAPPED}
    for key in FROZEN:
        result[key] = None
    return result
