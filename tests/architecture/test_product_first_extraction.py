"""Shared packages must prove product-first extraction before implementation.

ADR-0006's 2026-08-08 amendment turns ERP/Sub from inspiration into mandatory
source evidence.  This gate makes a missing dossier, missing product audit, or
new unresolved package fail in the fast architecture suite.  The existing debt
map is exact and may only shrink.

A dossier's `status` records the EVIDENCE LEVEL, never permission to be a shared
module.  ADR-0006's 2026-08-12 amendment separates the two: a second consumer
proves reuse, it does not decide placement.  Requiring two before sharing does
not delay a vendor-side capability whose data-plane consumer is revoked on
purpose — it forbids it.

All three states below permit a shared module.  They differ in what is proven:

``audit-complete``
    The inventory ran and the unit was drawn deliberately.  Nothing has adopted
    it.  Requires an audited `source_mode`, at least ONE concrete
    `candidate_consumers` entry, and ZERO `contract_consumers`.
``adopted``
    One real consumer is on the contract and its first cutover is complete.
    Requires an audited `source_mode` and exactly one `contract_consumers`
    entry.  It is a distinct state rather than a loosened `audit-complete`
    because a package with a live consumer that still claims "nothing has
    adopted it" makes a false statement about the fleet, and hides the first
    cutover ADR-0017 calls the scarce resource.
``reuse-proven``
    Two or more independent consumers exercise the same contract.  Requires an
    audited `source_mode` and two or more `contract_consumers`.  Formerly
    ``approved``; renamed because the old name described a permission, which is
    what this state no longer grants.
``PRE_RULE_DEBT[package]``
    Grandfathered.  The map is exact and only shrinks, and this is deliberately
    NOT the same claim as ``audit-complete`` (ADR-0018: "grandfathered" must
    stay distinguishable from "reviewed and correct").

The ratchet runs both ways.  A package may not claim more than its consumers
prove, and may not sit in a state weaker than its evidence supports: one
consumer forces ``adopted``, two force ``reuse-proven``.
"""

from __future__ import annotations

import ast
import copy
import re
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from tests.architecture import adoption_evidence as evidence_schema

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGES_DIR = PROJECT_ROOT / "packages"

VALID_CLASSIFICATIONS = {
    "universal-facility",
    "presentation-foundation",
    "optional-module",
    # A distribution a product CALLS rather than installs: it speaks an external
    # protocol and holds nothing. The three values above all describe something
    # INSTALLED, and calling such a package an `optional-module` sends a reader
    # looking for a manifest, a `mod_*` namespace and a lineage that do not
    # exist (ADR-0006, 2026-08-14 amendment).
    #
    # This is a GOVERNED value, not an accepted string: see
    # `stateless_package_violations` below for the four properties the word has
    # to mean, checked generically against whatever package claims it.
    "stateless-protocol-adapter",
    # A catalogue of one owner's capability contracts: canonical schema bytes
    # and their digests, and nothing that runs. It shares the adapter's four
    # properties, so those are checked identically — but it is NOT an adapter,
    # because an adapter EXISTS to reach a provider (`dotmac-auth-oidc` ships
    # `transport.py` and declares `httpx`) while a catalogue reaches nothing at
    # all. That fifth property is the one the separate word buys, and it is
    # checked here rather than only in the release lane, so a catalogue that
    # grew a provider client is caught by its CLASSIFICATION and not merely by
    # whether someone remembered to allowlist it (ADR-0006, 2026-08-26
    # amendment; the connector lane's "profile, not a classification" ruling
    # stands where the properties really are identical).
    "stateless-contract-catalogue",
}

STATELESS_ADAPTER = "stateless-protocol-adapter"
CONTRACT_CATALOGUE = "stateless-contract-catalogue"

# Import roots that mean "this package reaches off-box after all". An adapter
# is ALLOWED these; a contract catalogue is not.
NETWORK_ROOTS = frozenset(
    {"httpx", "requests", "aiohttp", "urllib", "urllib3", "socket", "subprocess"}
)

# Import roots that mean "this holds rows after all".
PERSISTENCE_ROOTS = frozenset({"sqlalchemy", "alembic", "psycopg", "asyncpg"})

# The two declarations that make a module STATEFUL (hard rule 14). Matched as
# assignment targets or keyword arguments, never as text — a docstring saying
# "declares no short_code" must not trip its own rule.
_LINEAGE_DECLARATIONS = frozenset({"short_code", "migration_prefix"})
VALID_SOURCE_MODES = {
    "product-first",
    "greenfield-after-inventory",
    "historical-mixed",
    "unresolved",
}
# The two modes that assert the ERP/Sub inventory actually happened.  Any status
# that claims the audit was done has to be backed by one of them.
AUDITED_SOURCE_MODES = {"product-first", "greenfield-after-inventory"}

# The evidence ladder (ADR-0006, 2026-08-12).  Every one of these permits a
# shared module; they differ only in what has been proven about reuse.
EVIDENCE_STATES = ("audit-complete", "adopted", "reuse-proven")


def _state_for(consumer_count: int) -> str:
    """The one evidence state a given number of contract consumers supports.

    Exact rather than a floor, in both directions.  A floor would let a package
    with two consumers keep claiming `audit-complete` forever, which is how the
    previous model let the gate quietly stop meaning anything.
    """
    if consumer_count == 0:
        return "audit-complete"
    if consumer_count == 1:
        return "adopted"
    return "reuse-proven"


# These packages predate the product-first dossier gate.  Keeping the status
# map exact prevents "temporary" audit debt from becoming the default for the
# next package.  A row is deleted when that package first reaches an evidence
# state — `adopted` for most, since leaving debt is what a real cutover proves.
PRE_RULE_DEBT = {
    "dotmac-kernel": "historical-pre-rule",
    # "dotmac-ui" was deleted here on 2026-08-13: it reached `reuse-proven` once
    # dotmac_sub and dotmac_academy_app both landed the released 0.1.0a3 on their
    # default branches, joining the reference assembly as the third contract
    # consumer of the TOKEN contract.  The row's removal is the ratchet shrinking
    # as designed -- re-adding it would re-grant a retired exemption.  Note the
    # scope at deletion time: the evidence covered tokens and compiled assets.
    # The component slice later reached reuse-proven independently through ERP
    # and Sub; that strengthens the dossier and does not revive this exemption.
}

REQUIRED_TEXT_FIELDS = {
    "package",
    "classification",
    "status",
    "source_mode",
    "owner",
    "contract",
    "first_cutover",
    "shadow_and_drift",
    "local_copy_retirement",
    "next_action",
}
REQUIRED_LIST_FIELDS = {
    "source_repositories",
    "source_paths",
    "preserved_tests",
    "inventory_evidence",
}


class ExtractionDossierError(AssertionError):
    """The package cannot pass the product-first extraction gate."""


def _required_slice_names(directory_name: str) -> set[str]:
    """Slices a package MUST declare, probed from what it actually PUBLISHES.

    Without this, a dossier can delete an inconvenient slice and restore a
    stronger headline: the gate would happily validate the remaining rows. The
    binding is to the live published surface, so the dossier cannot disagree
    with the package about which contracts exist.
    """
    if directory_name != "dotmac-ui":
        return set()

    import dotmac_ui

    required = {"tokens"}
    # A published component class means the component CONTRACT exists, whatever
    # the dossier says about it.
    if dotmac_ui.PUBLISHED_COMPONENT_CLASSES:
        required.add("components")
    # MAP_FRAME is its own evidence slice: the empty-state component already
    # has two released consumers while this contract intentionally has none.
    # Treating both as one row would let the mature component hide the new
    # contract's weaker evidence level.
    if dotmac_ui.MAP_FRAME in dotmac_ui.COMPONENTS:
        required.add("map-frame")
    return required


def _shared_package_dirs() -> list[Path]:
    return sorted(
        path
        for path in PACKAGES_DIR.iterdir()
        if path.is_dir() and (path / "pyproject.toml").is_file()
    )


def _reference_problems(field: str, references: list[Any]) -> list[str]:
    """`repo:path` shape, and local paths must actually exist.

    One implementation, used by the package-level fields and by every slice —
    two copies would drift, and a slice citing a deleted test is exactly the
    evidence claim this gate exists to refuse.
    """
    problems: list[str] = []
    for reference in references:
        if not isinstance(reference, str) or not reference.strip():
            problems.append(f"{field} entries must be non-empty strings")
            continue
        if ":" not in reference:
            problems.append(f"{field} entries must use repository:path references")
            continue
        repository, relative_path = reference.split(":", 1)
        if not repository.strip() or not relative_path.strip():
            problems.append(f"{field} entries must use repository:path references")
            continue
        if (
            repository == "dotmac_starter_mt"
            and not (PROJECT_ROOT / relative_path).exists()
        ):
            problems.append(f"{field} local reference does not exist: {reference}")
    return problems


def stateless_package_violations(
    package_dir: Path, *, label: str = "stateless adapter"
) -> list[str]:
    """The four properties a STATELESS classification has to MEAN.

    Shared by `stateless-protocol-adapter` and `stateless-contract-catalogue`:
    both hold nothing, install nothing, own no lineage and allocate no
    namespace, so checking those four twice would be two spellings of one rule.
    What separates the two is the FIFTH property, network reach, which
    `contract_catalogue_violations` adds on top — see `NETWORK_ROOTS`.

    `label` only names the classification in the message. It never changes what
    is checked, so the two callers cannot drift apart on the shared four.

    Generic over whatever package declares the classification — it takes a
    directory, not a name, so it governs the next such package as much as the
    first. Keyed on the DECLARED classification, so a stateful module is out of
    scope rather than accidentally held to a rule it should fail.

    A pure function over a directory tree so the sensitivity proof can build a
    synthetic package and show the checker firing (ADR-0018: a guard that cannot
    be shown to bite is not a guard).
    """
    problems: list[str] = []

    for path in sorted(package_dir.rglob("*.py")):
        rel = path.relative_to(package_dir)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - a broken file fails elsewhere
            continue

        for node in ast.walk(tree):
            # 1. Nothing to install. AST-only: this package's docstrings SAY
            #    "declares no ModuleManifest" on purpose, and a substring scan
            #    would fail on its own explanation.
            if isinstance(node, ast.Name) and node.id == "ModuleManifest":
                problems.append(
                    f"{rel}:{node.lineno} references ModuleManifest — a "
                    f"{label} is CALLED, not installed"
                )
            # 2. No lineage (hard rule 14's stateless shape). A real declaration
            #    is an assignment target or a keyword argument, never prose.
            if isinstance(node, ast.keyword) and node.arg in _LINEAGE_DECLARATIONS:
                problems.append(
                    f"{rel} passes {node.arg!r} — that is the STATEFUL shape; "
                    f"a {label} declares neither"
                )
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Name)
                        and target.id in _LINEAGE_DECLARATIONS
                    ):
                        problems.append(
                            f"{rel}:{node.lineno} assigns {target.id!r} — that is "
                            f"the STATEFUL shape; a {label} declares neither"
                        )
            # 4. No persistence.
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                root = name.split(".")[0]
                if root in PERSISTENCE_ROOTS:
                    problems.append(
                        f"{rel}:{node.lineno} imports {root!r} — a {label} "
                        "that grew persistence has become a module without "
                        "changing its dossier"
                    )
                if root == "dotmac_kernel" and "ModuleManifest" in (
                    alias.name for alias in getattr(node, "names", [])
                ):
                    problems.append(
                        f"{rel}:{node.lineno} imports ModuleManifest — a "
                        f"{label} is CALLED, not installed"
                    )

    # 2b. No migrations tree.
    for migrations in package_dir.rglob("migrations"):
        if migrations.is_dir():
            problems.append(
                f"{migrations.relative_to(package_dir)} exists — a {label} "
                "owns no lineage"
            )

    # 3. No namespace allocation. An allocation is permanent once added, so one
    #    made for a package that will never own a schema cannot be retracted.
    distribution = package_dir.name
    if _ledger_allocates(distribution):
        problems.append(
            f"{distribution} holds a MIGRATION_OWNER_LEDGER allocation — a "
            f"{label} owns no schema"
        )
    return problems


def contract_catalogue_violations(package_dir: Path) -> list[str]:
    """The FIVE properties `stateless-contract-catalogue` has to MEAN.

    The shared four, plus the one that earns the separate word: a catalogue
    reaches nothing. An adapter is allowed a provider client — that is what an
    adapter IS, and `dotmac-auth-oidc` declares `httpx` in its own release
    entry. A catalogue is held data: canonical schema bytes and the digests
    that attest them. A network import in one is the point; in the other it is
    a package that has quietly stopped being a catalogue.

    Checked on the DECLARED classification rather than on lane membership, so
    an unlisted catalogue is governed exactly as much as an allowlisted one.
    """
    problems = stateless_package_violations(package_dir, label="contract catalogue")

    for path in sorted(package_dir.rglob("*.py")):
        rel = path.relative_to(package_dir)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - a broken file fails elsewhere
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                root = name.split(".")[0]
                if root in NETWORK_ROOTS:
                    problems.append(
                        f"{rel}:{node.lineno} imports {root!r} — a contract "
                        "catalogue describes semantics and reaches nothing; a "
                        "package that calls a provider is an ADAPTER and "
                        "belongs in that lane, where the network is expected"
                    )
    return problems


def _ledger_allocates(distribution: str) -> bool:
    """True if the kernel ledger allocates a namespace to `distribution`."""
    try:
        from dotmac_kernel.namespaces import MIGRATION_OWNER_LEDGER
    except Exception:  # pragma: no cover - kernel absent is a different failure
        return False
    import_name = distribution.replace("-", "_")
    for owner in MIGRATION_OWNER_LEDGER:
        for attribute in ("distribution", "package", "import_name", "owner"):
            value = getattr(owner, attribute, None)
            if isinstance(value, str) and value.replace("-", "_") == import_name:
                return True
    return False


def _load_toml(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


#: States that assert a product RAN the module. `audit-complete` does not.
RAN_IN_A_PRODUCT = frozenset({"adopted", "reuse-proven"})


def _adoption_evidence_problems(
    status: str,
    dossier: Mapping[str, object],
    *,
    distribution: str = "",
    schema_marker: object = None,
    where: str = "",
) -> list[str]:
    """`adopted` must cite what ran, as AdoptionEvidenceV1 — typed, and frozen
    to an immutable commit.

    The consumer ratchet above already refuses claiming more or less adoption
    than the consumer COUNT proves.  It says nothing about whether the claim is
    CHECKABLE, and that gap is how two dossiers came to carry `adopted` with no
    `adoption_evidence` key at all.

    Until 2026-08-29 this function closed that gap only as far as
    ADDRESSABILITY: a reference had to split on a colon into a repository and a
    non-empty identity.  Three defects landed on one field of one dossier inside
    a single day and every one of them split on a colon — a pin that was stale
    within twenty minutes, a seven-hex abbreviation of a BRANCH name, and a CI
    run id carrying no commit, so nothing about the string changed when the tree
    it exercised did.  `dotmac_governance` ADR 0013 (`AGENTS.md` rule 30) had
    already ruled a branch name and "latest" out as coordinates; the gap was
    never in the policy, it was that addressability and immutability are
    different tests and nobody noticed.

    The shape rules now live in `tests.architecture.adoption_evidence`, whose
    docstring carries the reasoning and the explicitly UNMONITORED half.  This
    function keeps only the status coupling, because a permission-adjacent
    ratchet and a shape check must not be the same test.
    """
    if status not in RAN_IN_A_PRODUCT:
        # An unadopted dossier still may not carry a defective row: shape is not
        # earned by claiming less. Only the "must cite something" half is
        # status-conditional.
        return evidence_schema.evidence_problems(
            rows=dossier.get("adoption_evidence") or [],
            pointers=dossier.get("adoption_evidence_pointer"),
            schema_marker=schema_marker,
            distribution=distribution,
            where=where,
        )
    evidence = dossier.get("adoption_evidence")
    if evidence is None:
        return [f"{status} carries no adoption_evidence key at all"]
    if not isinstance(evidence, list) or not evidence:
        return [f"{status} must cite the adoption evidence it claims"]
    return evidence_schema.evidence_problems(
        rows=evidence,
        pointers=dossier.get("adoption_evidence_pointer"),
        schema_marker=schema_marker,
        distribution=distribution,
        where=where,
    )


#: What a named product DOES with the capability, as a closed vocabulary.
#:
#: `source_repositories` cannot answer this and never could: it records which
#: repositories were INVENTORIED, not which hold a writer. Reading it as
#: "has a writer" makes every inventoried-but-clean product look like debt, and
#: reading `local_copy_retirement` prose instead is worse — a sentence is not a
#: claim a checker can compare.
#:
#: Governance cites these values across the repository boundary, so the
#: vocabulary is closed and each member means exactly one thing:
#:
#: - `qualifying_source` — the implementation being extracted FROM. Has writers
#:   by definition, and they retire at its own cutover.
#: - `legacy_writer` — writes the capability today and must stop. This is the
#:   state a "no writer here" claim is refuted by.
#: - `no_writer` — does not write it. The only state that supports such a claim.
#: - `inventory_only` — was read while inventorying and writes nothing. Distinct
#:   from `no_writer` because it records that somebody LOOKED, which is what
#:   makes the claim checkable rather than merely unrefuted.
#: A claim measured against a moving branch is not a claim.
IMMUTABLE_REVISION = re.compile(r"^[0-9a-f]{40}$")

PRODUCT_WRITER_STATES = frozenset(
    {"qualifying_source", "legacy_writer", "no_writer", "inventory_only"}
)

PRODUCT_WRITER_FIELDS = frozenset(
    {"product", "writer_state", "retirement_required", "revision", "evidence_paths"}
)


def _product_writer_problems(dossier: dict[str, Any]) -> list[str]:
    """Validate `[[product_writers]]`, the typed claim Governance reads.

    Absent entirely is allowed and is NOT the same as "no product writes this".
    A consumer that cannot find the entry it needs must fail as UNKNOWN, which
    is the whole reason the state is typed: silence and a claim of absence have
    to be distinguishable, or a missing file reads as a clean bill of health.
    """

    entries = dossier.get("product_writers")
    if entries is None:
        return []
    problems: list[str] = []
    if not isinstance(entries, list) or not entries:
        return ["product_writers must be a non-empty array of tables when present"]

    pin_sets: dict[str, dict[str, list[str]]] = {}
    for field in ("source_revisions", "revalidation_revisions"):
        pins: dict[str, list[str]] = {}
        for coordinate in dossier.get(field) or []:
            if isinstance(coordinate, str) and ":" in coordinate:
                source, revision = coordinate.split(":", 1)
                pins.setdefault(source, []).append(revision)
        pin_sets[field] = pins

    seen: set[str] = set()
    for index, entry in enumerate(entries):
        where = f"product_writers[{index}]"
        if not isinstance(entry, dict):
            problems.append(f"{where} must be a table")
            continue
        unknown = sorted(set(entry) - PRODUCT_WRITER_FIELDS)
        if unknown:
            problems.append(f"{where} has unknown fields: {', '.join(unknown)}")

        product = entry.get("product")
        if not isinstance(product, str) or not product.strip():
            problems.append(f"{where}.product must be a non-empty string")
        else:
            if product in seen:
                problems.append(f"{where}.product duplicates {product!r}")
            seen.add(product)
            declared = dossier.get("source_repositories")
            if isinstance(declared, list) and product not in declared:
                problems.append(
                    f"{where}.product {product!r} is not in source_repositories; a "
                    "writer claim about a product nobody inventoried is a guess"
                )

        state = entry.get("writer_state")
        if state not in PRODUCT_WRITER_STATES:
            problems.append(
                f"{where}.writer_state must be one of "
                f"{', '.join(sorted(PRODUCT_WRITER_STATES))}"
            )

        retirement = entry.get("retirement_required")
        if not isinstance(retirement, bool):
            problems.append(f"{where}.retirement_required must be a boolean")
        elif state in {"no_writer", "inventory_only"} and retirement:
            problems.append(
                f"{where} claims {state!r} and retirement_required=true; nothing "
                "that writes nothing has anything to retire"
            )
        elif state == "legacy_writer" and not retirement:
            problems.append(
                f"{where} claims legacy_writer and retirement_required=false; a "
                "writer that never retires is a second permanent authority"
            )

        revision = entry.get("revision")
        if not isinstance(revision, str) or not IMMUTABLE_REVISION.fullmatch(revision):
            problems.append(
                f"{where}.revision must be an immutable 40-character commit — the "
                "claim is only true of the tree it was measured against"
            )
        elif isinstance(product, str):
            product_pins = pin_sets["revalidation_revisions"].get(
                product, pin_sets["source_revisions"].get(product, [])
            )
            if product_pins != [revision]:
                problems.append(
                    f"{where}.revision must equal {product!r}'s one effective "
                    f"audit pin; found {product_pins!r}"
                )

        evidence = entry.get("evidence_paths")
        if not isinstance(evidence, list) or not all(
            isinstance(item, str) and item.strip() for item in evidence
        ):
            problems.append(f"{where}.evidence_paths must be a string list")
        elif state in {"qualifying_source", "legacy_writer"} and not evidence:
            problems.append(
                f"{where} claims {state!r} and cites no evidence path; the paths "
                "are what a reviewer checks the claim against"
            )
    return problems


def _validate_dossier(
    dossier: dict[str, Any],
    *,
    directory_name: str,
    distribution_name: str,
    package_dir: Path | None = None,
) -> None:
    problems: list[str] = []

    schema_version = dossier.get("schema_version")
    if schema_version not in (1, 2):
        problems.append("schema_version must be 1 or 2")

    for field in sorted(REQUIRED_TEXT_FIELDS):
        value = dossier.get(field)
        if not isinstance(value, str) or not value.strip():
            problems.append(f"{field} must be a non-empty string")

    for field in sorted(REQUIRED_LIST_FIELDS):
        value = dossier.get(field)
        if (
            not isinstance(value, list)
            or not value
            or not all(isinstance(item, str) and item.strip() for item in value)
        ):
            problems.append(f"{field} must be a non-empty string list")

    for field in ("source_paths", "preserved_tests"):
        references = dossier.get(field)
        if not isinstance(references, list):
            continue
        problems.extend(_reference_problems(field, references))

    problems.extend(_product_writer_problems(dossier))

    inventory_references = dossier.get("inventory_evidence")
    if isinstance(inventory_references, list):
        for reference in inventory_references:
            if isinstance(reference, str) and not (PROJECT_ROOT / reference).is_file():
                problems.append(f"inventory evidence does not exist: {reference}")

    contract_consumers = dossier.get("contract_consumers")
    if not isinstance(contract_consumers, list) or not all(
        isinstance(consumer, str) and consumer.strip()
        for consumer in contract_consumers
    ):
        problems.append("contract_consumers must be a string list")

    package = dossier.get("package")
    if package != directory_name or package != distribution_name:
        problems.append(
            "package must match both the package directory and pyproject distribution"
        )

    classification = dossier.get("classification")
    if classification not in VALID_CLASSIFICATIONS:
        problems.append(
            f"classification must be one of {sorted(VALID_CLASSIFICATIONS)}"
        )
    if classification == STATELESS_ADAPTER and package_dir is not None:
        problems.extend(stateless_package_violations(package_dir))
    if classification == CONTRACT_CATALOGUE and package_dir is not None:
        problems.extend(contract_catalogue_violations(package_dir))

    source_mode = dossier.get("source_mode")
    if source_mode not in VALID_SOURCE_MODES:
        problems.append(f"source_mode must be one of {sorted(VALID_SOURCE_MODES)}")

    repositories = dossier.get("source_repositories")
    if isinstance(repositories, list) and not {"dotmac_erp", "dotmac_sub"}.issubset(
        repositories
    ):
        problems.append(
            "source_repositories must show both ERP and Sub were inventoried"
        )

    status = dossier.get("status")

    # Before adoption, a dossier with no named candidate is a package built for
    # nobody, which is the speculative extraction ADR-0006 section 5 exists to
    # stop. After adoption, the real contract consumer is the evidence; forcing
    # a non-empty candidate list would require inventing future demand or
    # relabelling the existing consumer as one it cannot truthfully be.
    candidate_consumers = dossier.get("candidate_consumers")
    if not isinstance(candidate_consumers, list) or not all(
        isinstance(consumer, str) and consumer.strip()
        for consumer in candidate_consumers
    ):
        problems.append("candidate_consumers must be a string list")
    elif status == "audit-complete" and not candidate_consumers:
        problems.append(
            "candidate_consumers must name at least one concrete consumer "
            "before adoption"
        )

    expected_debt = PRE_RULE_DEBT.get(directory_name)
    consumers = dossier.get("contract_consumers")
    consumer_count = len(set(consumers)) if isinstance(consumers, list) else 0

    # ── Contract slices (schema 2) ──────────────────────────────────────────
    # A package may publish more than one CONTRACT, and their evidence differs.
    # `dotmac-ui` publishes semantic tokens and a Jinja component library. The
    # two slices reached reuse on different dates and may diverge again as new
    # contracts are published. A single package-level assertion cannot preserve
    # that evidence history. Schema 2 gives each slice its own status, sources,
    # tests, consumers and retirement gate, while the package headline remains
    # DERIVED rather than asserted.
    slices = dossier.get("slices")
    if schema_version == 2:
        if not isinstance(slices, list) or not slices:
            problems.append("schema_version 2 requires a non-empty [[slices]] list")
            slices = []
    elif slices is not None:
        problems.append("slices require schema_version 2")
        slices = []
    else:
        slices = []

    slice_states: list[str] = []
    slice_consumers: set[str] = set()
    seen_names: set[str] = set()
    for index, entry in enumerate(slices):
        label = f"slice[{index}]"
        if not isinstance(entry, dict):
            problems.append(f"{label} must be a table")
            continue
        name = entry.get("name")
        label = f"slice {name!r}" if isinstance(name, str) else label
        for field in ("name", "contract", "status", "local_copy_retirement"):
            value = entry.get(field)
            if not isinstance(value, str) or not value.strip():
                problems.append(f"{label}: {field} must be a non-empty string")
        for field in ("source_paths", "preserved_tests"):
            value = entry.get(field)
            if not isinstance(value, list) or not value:
                problems.append(f"{label}: {field} must be a non-empty string list")
                continue
            # Same strictness as the package-level fields: a `repo:path`
            # reference, and a local one has to exist. A list of arbitrary
            # strings would let a slice cite evidence that was never there.
            problems.extend(
                f"{label}: {problem}" for problem in _reference_problems(field, value)
            )

        if isinstance(name, str):
            if name in seen_names:
                problems.append(f"duplicate slice name {name!r}")
            seen_names.add(name)

        # A slice that claims a product RAN it owes the same addressable
        # evidence the package level owes. Checking only the headline would miss
        # exactly this shape: `dotmac-ui` derives its headline from the WEAKEST
        # slice, so two `reuse-proven` slices sat behind an `audit-complete`
        # package status where no package-level rule could see them.
        entry_status = entry.get("status")
        if isinstance(entry_status, str):
            problems.extend(
                f"{label}: {problem}"
                for problem in _adoption_evidence_problems(
                    entry_status,
                    entry,
                    distribution=distribution_name,
                    # A slice inherits the package's declaration: the version
                    # marker describes one file, not one contract slice.
                    schema_marker=entry.get(
                        evidence_schema.SCHEMA_MARKER,
                        dossier.get(evidence_schema.SCHEMA_MARKER),
                    ),
                )
            )

        entry_consumers = entry.get("contract_consumers")
        if not isinstance(entry_consumers, list) or not all(
            isinstance(c, str) for c in entry_consumers
        ):
            problems.append(f"{label}: contract_consumers must be a string list")
            entry_consumers = []
        # Reference consumers are recorded SEPARATELY and never count: the
        # assembly that owns a package proves the wiring works, not that anyone
        # independent chose it (ADR-0006 § 5).
        reference = entry.get("reference_consumers", [])
        if not isinstance(reference, list) or not all(
            isinstance(c, str) for c in reference
        ):
            problems.append(f"{label}: reference_consumers must be a string list")
            reference = []
        # Who this slice is FOR. Package-level `candidate_consumers` is not
        # enough: an audit-complete slice would otherwise be satisfied by a
        # candidate named for a different contract entirely.
        candidates = entry.get("candidate_consumers")
        if not isinstance(candidates, list) or not all(
            isinstance(c, str) and c.strip() for c in candidates
        ):
            problems.append(f"{label}: candidate_consumers must be a string list")
            candidates = []
        elif not set(candidates):
            # ADR-0006 (2026-08-12): `audit-complete` requires "at least one
            # concrete candidate consumer". ONE is the floor, and the amendment
            # says why it was lowered rather than dropped: a dossier with no
            # named candidate is a package built for nobody. TWO is the
            # threshold for `reuse-proven`, and it counts CONTRACT consumers,
            # not candidates -- requiring two candidates here was stricter than
            # the decision this gate enforces.
            problems.append(
                f"{label}: candidate_consumers must name at least one concrete "
                "product this slice is being built for"
            )
        # A candidate is a product that does NOT have this contract yet. An
        # existing consumer and a reference consumer both already do, in their
        # different ways, so neither can stand in as future demand.
        already = set(candidates) & set(entry.get("contract_consumers") or [])
        if already:
            problems.append(
                f"{label}: {sorted(already)} listed as candidates but already "
                "consume this slice"
            )
        also_reference = set(candidates) & set(reference)
        if also_reference:
            problems.append(
                f"{label}: {sorted(also_reference)} listed as candidates but "
                "already recorded as reference consumers -- reference proof is "
                "not future demand"
            )

        overlap = set(entry_consumers) & set(reference)
        if overlap:
            problems.append(
                f"{label}: {sorted(overlap)} counted as both an independent and a "
                "reference consumer — reference proof is not adoption"
            )

        entry_status = entry.get("status")
        required = _state_for(len(set(entry_consumers)))
        if entry_status in EVIDENCE_STATES and entry_status != required:
            problems.append(
                f"{label}: status is {entry_status} with "
                f"{len(set(entry_consumers))} independent consumer(s); that "
                f"evidence level is exactly {required}"
            )
        elif entry_status not in EVIDENCE_STATES and isinstance(entry_status, str):
            problems.append(f"{label}: status must be one of {list(EVIDENCE_STATES)}")
        if isinstance(entry_status, str):
            slice_states.append(entry_status)
        slice_consumers |= set(entry_consumers)

    required_names = _required_slice_names(directory_name)
    missing_slices = required_names - seen_names
    if schema_version == 2 and missing_slices:
        problems.append(
            f"the package publishes contracts with no slice: {sorted(missing_slices)}"
            " — a slice cannot be deleted to restore a stronger headline"
        )

    if schema_version == 2 and seen_names:
        # Legacy top-level prose describes ONE contract; under schema 2 the
        # headline describes the weakest slice, so the summary has to cover all
        # of them or it silently keeps meaning the strongest.
        for field in ("contract", "first_cutover"):
            text = dossier.get(field)
            if not isinstance(text, str):
                continue
            unmentioned = sorted(n for n in seen_names if n not in text)
            if unmentioned:
                problems.append(
                    f"{field} is an aggregate under schema 2 but does not mention "
                    f"slice(s): {unmentioned}"
                )

    if slices and not problems:
        # The headline is the WEAKEST slice.  A package is only as proven as its
        # least-proven published contract, and deriving it means the summary can
        # never drift from the rows beneath it.
        weakest = min(slice_states, key=EVIDENCE_STATES.index)
        if status != weakest:
            problems.append(
                f"status is {status}, but the weakest contract slice is "
                f"{weakest} — a package is only as proven as its least-proven "
                "published contract"
            )
        if isinstance(consumers, list) and set(consumers) != slice_consumers:
            problems.append(
                "contract_consumers must be exactly the union of the slices' "
                f"independent consumers: {sorted(slice_consumers)}"
            )

    if status in EVIDENCE_STATES:
        if source_mode not in AUDITED_SOURCE_MODES:
            problems.append(
                f"{status} claims the inventory was done; source_mode must be "
                "product-first or greenfield-after-inventory to back that"
            )
        if expected_debt is not None:
            problems.append(
                "remove this package from PRE_RULE_DEBT when its dossier claims "
                "an evidence state"
            )
        # The two-directional ratchet.  Claiming MORE than the consumers prove is
        # the obvious failure; claiming LESS is the one the two-state model
        # allowed, and it is worse, because a package with a live consumer that
        # still says "nothing has adopted it" is a false statement about the
        # fleet rather than a missing one.
        required = _state_for(consumer_count)
        if not slices and status != required:
            problems.append(
                f"status is {status} with {consumer_count} contract consumer(s); "
                f"that evidence level is exactly {required}"
            )
        problems.extend(
            _adoption_evidence_problems(
                status,
                dossier,
                distribution=distribution_name,
                schema_marker=dossier.get(evidence_schema.SCHEMA_MARKER),
            )
        )
    elif status != expected_debt:
        problems.append(
            "only the exact PRE_RULE_DEBT map may carry an unresolved or "
            "historical status"
        )

    if problems:
        raise ExtractionDossierError("; ".join(problems))


def test_every_shared_distribution_has_a_valid_extraction_dossier() -> None:
    package_dirs = _shared_package_dirs()
    assert package_dirs, "no shared package distributions found"

    for package_dir in package_dirs:
        dossier_path = package_dir / "EXTRACTION.toml"
        assert dossier_path.is_file(), f"{package_dir.name} has no EXTRACTION.toml"
        pyproject = _load_toml(package_dir / "pyproject.toml")
        distribution_name = pyproject["tool"]["poetry"]["name"]
        _validate_dossier(
            _load_toml(dossier_path),
            directory_name=package_dir.name,
            distribution_name=distribution_name,
            package_dir=package_dir,
        )


# --------------------------------------------------------------------------
# typed product writer claims — what Governance reads across the repo boundary
# --------------------------------------------------------------------------


def _writers(**overrides: Any) -> dict[str, Any]:
    entry = {
        "product": "dotmac_sub",
        "writer_state": "no_writer",
        "retirement_required": False,
        "revision": "883a0ff1aff89e3ea5e241897a4b965527e9bce1",
        "evidence_paths": [],
    }
    entry.update(overrides)
    return {
        "source_repositories": ["dotmac_sub", "dotmac_erp"],
        "source_revisions": ["dotmac_sub:883a0ff1aff89e3ea5e241897a4b965527e9bce1"],
        "product_writers": [entry],
    }


def test_an_absent_claim_is_allowed_and_is_not_a_claim_of_absence() -> None:
    """The distinction the whole design rests on.

    A dossier with no `product_writers` is valid. It does NOT say "no product
    writes this" — it says nothing, and a consumer needing an answer must fail
    as UNKNOWN. Treating silence as a clean bill of health is the failure that
    let two capabilities be rostered as having no Sub writer while their own
    dossiers named Sub writers requiring retirement.
    """

    assert _product_writer_problems({"source_repositories": ["dotmac_sub"]}) == []


def test_the_expenses_failure_would_now_be_refused() -> None:
    """SENSITIVITY: the original defect, as a typed claim.

    Expenses was rostered "no ISP writer in scope" while Sub held two writers
    its own `local_copy_retirement` required to ratchet to zero. Under the
    typed vocabulary that is `legacy_writer` + `retirement_required = true`,
    which a `no_product_writer` rationale cannot cite.
    """

    dossier = _writers(
        writer_state="legacy_writer",
        retirement_required=True,
        evidence_paths=["app/services/field/expense_requests.py"],
    )
    assert _product_writer_problems(dossier) == []
    claim = dossier["product_writers"][0]
    assert claim["writer_state"] == "legacy_writer"
    assert claim["retirement_required"] is True


def test_the_surveys_failure_would_now_be_refused() -> None:
    """SENSITIVITY: Sub was cutover 1 while the roster said nothing was scheduled."""

    dossier = _writers(
        writer_state="qualifying_source",
        retirement_required=True,
        evidence_paths=["app/services/surveys.py"],
    )
    assert _product_writer_problems(dossier) == []
    assert dossier["product_writers"][0]["writer_state"] == "qualifying_source"


def test_an_inventory_only_product_is_valid_and_supports_the_claim() -> None:
    """SENSITIVITY: the case that must PASS.

    A product read while inventorying that writes nothing is the legitimate
    shape of "no writer here". It is distinct from `no_writer` because it
    records that somebody LOOKED — which is what makes the claim checkable
    rather than merely unrefuted. Refusing it would make the control unusable
    for exactly the capabilities it is meant to clear.
    """

    assert _product_writer_problems(_writers(writer_state="inventory_only")) == []


def test_a_writer_that_never_retires_is_refused() -> None:
    problems = _product_writer_problems(
        _writers(
            writer_state="legacy_writer",
            retirement_required=False,
            evidence_paths=["app/x.py"],
        )
    )
    assert any(
        "second permanent authority" in problem for problem in problems
    ), problems


def test_a_non_writer_with_retirement_work_is_refused() -> None:
    problems = _product_writer_problems(
        _writers(writer_state="no_writer", retirement_required=True)
    )
    assert any("anything to retire" in problem for problem in problems), problems


def test_a_writer_claim_without_evidence_paths_is_refused() -> None:
    problems = _product_writer_problems(
        _writers(
            writer_state="legacy_writer", retirement_required=True, evidence_paths=[]
        )
    )
    assert any("cites no evidence path" in problem for problem in problems), problems


def test_a_moving_revision_is_refused() -> None:
    """A claim measured against a branch is not a claim."""

    problems = _product_writer_problems(_writers(revision="main"))
    assert any(
        "immutable 40-character commit" in problem for problem in problems
    ), problems


def test_a_writer_revision_that_differs_from_its_source_pin_is_refused() -> None:
    """The typed row and dossier inventory cannot describe different trees."""

    dossier = _writers()
    dossier["source_revisions"] = [
        "dotmac_sub:1111111111111111111111111111111111111111"
    ]
    problems = _product_writer_problems(dossier)
    assert any("must equal" in problem for problem in problems), problems


def test_a_revalidated_writer_revision_uses_the_later_exact_pin() -> None:
    """Revalidation advances the claim without rewriting original provenance."""
    dossier = _writers()
    dossier["revalidation_revisions"] = [
        "dotmac_sub:3333333333333333333333333333333333333333"
    ]
    problems = _product_writer_problems(dossier)
    assert any("effective audit pin" in problem for problem in problems), problems

    dossier["product_writers"][0]["revision"] = (
        "3333333333333333333333333333333333333333"
    )
    assert _product_writer_problems(dossier) == []


def test_an_untyped_writer_state_is_refused_rather_than_ignored() -> None:
    problems = _product_writer_problems(_writers(writer_state="probably fine"))
    assert any(
        "writer_state must be one of" in problem for problem in problems
    ), problems


def test_a_claim_about_an_uninventoried_product_is_refused() -> None:
    """A writer claim about a product nobody inventoried is a guess."""

    problems = _product_writer_problems(_writers(product="dotmac_crm"))
    assert any(
        "not in source_repositories" in problem for problem in problems
    ), problems


# --------------------------------------------------------------------------
# AdoptionEvidenceV1 — the shape refusals, each with the defect it prevents
# --------------------------------------------------------------------------

#: A minimal well-formed dossier fragment.  Used as the NEGATIVE CONTROL for
#: every mutation below: a scanner over a clean tree passes for the wrong reason
#: unless a planted defect is shown to fail (ADR-0018 / rule 23).
_GOOD_COMMIT = "c0de423c2899f9372d70f6b20bd994de5b3e7ab5"


def _typed_evidence(distribution: str, **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "kind": "pinned_at",
        "repository": "dotmac_vendor_control_plane",
        "commit": _GOOD_COMMIT,
        "path": "pyproject.toml",
        "field": f"tool.poetry.dependencies.{distribution}.version",
        "expected": "0.1.0a1",
    }
    row.update(overrides)
    return {
        "adoption_evidence_schema": "v1",
        "adoption_evidence": [row],
        "adoption_evidence_pointer": [
            {
                "subject": "current_pin",
                "repository": "dotmac_vendor_control_plane",
                "paths": ["pyproject.toml", "poetry.lock"],
                "field": f"tool.poetry.dependencies.{distribution}.version",
            }
        ],
    }


def _evidence_problems(fragment: Mapping[str, Any], distribution: str) -> list[str]:
    return _adoption_evidence_problems(
        "adopted",
        fragment,
        distribution=distribution,
        schema_marker=fragment.get("adoption_evidence_schema"),
    )


def test_adopted_without_evidence_is_rejected() -> None:
    """SENSITIVITY for the status coupling, both halves."""
    assert _adoption_evidence_problems("adopted", {}) == [
        "adopted carries no adoption_evidence key at all"
    ]
    assert _adoption_evidence_problems("adopted", {"adoption_evidence": []}) == [
        "adopted must cite the adoption evidence it claims"
    ]
    # `audit-complete` asserts no run, so it is not asked to prove one.
    assert _adoption_evidence_problems("audit-complete", {}) == []


def test_a_well_formed_row_is_accepted() -> None:
    """The negative control. Without it, every mutation below could be passing
    because the checker rejects EVERYTHING, and the suite would look green while
    proving nothing."""
    assert (
        _evidence_problems(_typed_evidence("dotmac-ticketing"), "dotmac-ticketing")
        == []
    )


def test_a_moving_ref_is_refused() -> None:
    """MUTATION 1 — the coordinate that means something different tomorrow.

    `dotmac_erp:main@e1402902` merged into a live dossier and the old gate saw
    nothing, because it split on a colon. `dotmac_governance` ADR 0013 had
    already ruled a branch name out as a coordinate; the gate had simply never
    implemented it. Both spellings are covered: a bare moving ref, and one
    dressed up with an `@sha` suffix so it LOOKS pinned.
    """
    for moving in ("main", "HEAD", "latest", "default-branch"):
        bad = _typed_evidence("dotmac-ticketing", commit=moving)
        problems = _evidence_problems(bad, "dotmac-ticketing")
        assert any("moving ref" in p or "40-character" in p for p in problems), moving

    dressed = _typed_evidence("dotmac-ticketing", commit=f"main@{_GOOD_COMMIT}")
    assert any(
        "moving ref" in problem
        for problem in _evidence_problems(dressed, "dotmac-ticketing")
    )

    # And the same refusal on a LOCATOR. #495 kept `main@e1402902` "only as a
    # locator", which is a weaker role for the same bad coordinate.
    demoted = _typed_evidence("dotmac-ticketing", locator="dotmac_erp:main@e1402902")
    assert any(
        "moving ref" in problem
        for problem in _evidence_problems(demoted, "dotmac-ticketing")
    )


def test_a_short_sha_is_refused() -> None:
    """MUTATION 2 — the abbreviation that is unique only where the objects are.

    Two live examples: `dotmac_erp:main@e1402902` (8) and, in the auth-oidc
    dossier, `dotmac_workspace:main@ef38693` (7). Refused even though git
    resolves them, because this file is read by tools that have not cloned the
    repository, and because a truncation and a deliberate abbreviation are
    indistinguishable once written down.
    """
    truncations = [_GOOD_COMMIT[:n] for n in (7, 8, 12, 39)]
    # Over-length too: slicing past 40 silently returns the WHOLE commit, so a
    # naive `[:41]` case would pass for the wrong reason and prove nothing.
    for candidate in [*truncations, _GOOD_COMMIT + "ab"]:
        assert len(candidate) != 40, candidate
        problems = _evidence_problems(
            _typed_evidence("dotmac-ticketing", commit=candidate), "dotmac-ticketing"
        )
        assert any("abbreviated revision" in p for p in problems), candidate

    # Uppercase hex of the right length is still not the coordinate: two
    # spellings of one commit make two strings that never compare equal.
    shouty = _typed_evidence("dotmac-ticketing", commit=_GOOD_COMMIT.upper())
    assert _evidence_problems(shouty, "dotmac-ticketing")


def test_an_unrelated_run_is_refused() -> None:
    """MUTATION 3 — an attestation observed against a tree nobody cites.

    `dotmac_erp:workflow-run#33248839031` sat in the deployment-foundation
    dossier for a day beside a pin claim it never exercised: it was recorded for
    the a1-era tree, the pin rows were corrected to a2, and the run string did
    not change because a bare run id carries no commit. Requiring the
    attestation's `commit` to be one an assertion in the SAME dossier cites
    makes that drift fail the instant the assertions move.
    """
    good = _typed_evidence("dotmac-ticketing")
    good["adoption_evidence"].append(
        {
            "kind": "workflow_run",
            "repository": "dotmac_vendor_control_plane",
            "commit": _GOOD_COMMIT,
            "run_id": "33251009129",
            "observed": "success",
            "observed_at": "2026-08-29",
            "observed_by": "michaelayoade",
        }
    )
    assert _evidence_problems(good, "dotmac-ticketing") == []

    unrelated = copy.deepcopy(good)
    unrelated["adoption_evidence"][1]["commit"] = (
        "63c59133f3393c448756bedb04ecfa8a2c12187b"
    )
    assert any(
        "no assertion in this dossier cites" in problem
        for problem in _evidence_problems(unrelated, "dotmac-ticketing")
    )

    # A run attesting a repository this dossier makes no claim about at all —
    # the shape of `dotmac_sub:production-deploy#32009246911`, which sat in the
    # release-catalogue dossier with no Sub assertion anywhere near it.
    foreign = copy.deepcopy(good)
    foreign["adoption_evidence"][1]["repository"] = "dotmac_sub"
    assert any(
        "no assertion in this dossier cites that repository" in problem
        for problem in _evidence_problems(foreign, "dotmac-ticketing")
    )


def test_a_reference_that_does_not_support_its_claim_is_refused() -> None:
    """MUTATION 4 — claim/reference mismatch.

    A row can be perfectly formed, cite a real commit and a real field, and
    still be evidence for something else. Four shapes, four ways the mismatch
    shows:

    * a `pinned_at` addressing a DIFFERENT distribution's dependency entry — a
      true fact about a different subject;
    * a `field` of `exists`, which turns "the descriptor is there" into "the
      contract is spoken" and would let a v2 descriptor count as adoption of v1;
    * an attestation carrying `path`/`field`/`expected`, dressing an oracle
      lookup as a re-checkable file read so it is silently never checked;
    * a `contract_binding` whose `expected` names no pinned revision, so the
      binding cannot fail — which is the whole of rule 28.
    """
    wrong_subject = _typed_evidence(
        "dotmac-ticketing", field="tool.poetry.dependencies.dotmac-approvals.version"
    )
    assert any(
        "does not support the claim" in problem
        for problem in _evidence_problems(wrong_subject, "dotmac-ticketing")
    )

    pseudo = _typed_evidence("dotmac-ticketing", field="exists")
    assert any(
        "is not a real key" in problem
        for problem in _evidence_problems(pseudo, "dotmac-ticketing")
    )

    unparseable = _typed_evidence("dotmac-ticketing", path="src/assembly.py")
    assert any(
        "no structured extension" in problem
        for problem in _evidence_problems(unparseable, "dotmac-ticketing")
    )

    dressed_up = _typed_evidence("dotmac-ticketing")
    dressed_up["adoption_evidence"].append(
        {
            "kind": "deploy_run",
            "repository": "dotmac_vendor_control_plane",
            "commit": _GOOD_COMMIT,
            "run_id": "32022599873",
            "observed": "success",
            "observed_at": "2026-08-17",
            "observed_by": "michaelayoade",
            "path": "pyproject.toml",
            "field": "tool.poetry.dependencies.dotmac-ticketing.version",
            "expected": "0.1.0a1",
        }
    )
    assert any(
        "must not carry" in problem
        for problem in _evidence_problems(dressed_up, "dotmac-ticketing")
    )

    unbound = _typed_evidence(
        "dotmac-ticketing",
        kind="contract_binding",
        path=".github/workflows/conformance.yml",
        field="jobs.deployment.uses",
        expected="michaelayoade/dotmac_starter_mt/.github/workflows/x.yml@main",
    )
    assert any(
        "names no immutable coordinate" in problem
        for problem in _evidence_problems(unbound, "dotmac-ticketing")
    )


def test_free_text_evidence_can_never_come_back() -> None:
    """The ratchet. Migration is done in one change, so there is no shrink-only
    allowlist to maintain — a free-text entry is refused everywhere, from day
    one, and the refusal names itself."""
    regressed = {"adoption_evidence": ["dotmac_erp:main@e1402902"]}
    assert any(
        "free-text string" in problem
        for problem in _evidence_problems(regressed, "dotmac-ticketing")
    )
    assert evidence_schema.free_text_problems("", ["dotmac_erp:pull/415"])
    assert evidence_schema.free_text_problems("", [{"kind": "adopted"}]) == []


def test_an_unknown_kind_is_refused() -> None:
    """The thirteen-prefix accidental vocabulary. A kind nobody wrote a
    verification procedure for cannot be checked, so it must fail rather than
    pass through."""
    for unknown in ("local-copy-retired", "alembic-head", "catalog-artifact", None):
        bad = _typed_evidence("dotmac-ticketing", kind=unknown)
        assert any(
            "unknown kind" in problem
            for problem in _evidence_problems(bad, "dotmac-ticketing")
        ), unknown


def test_this_repository_cannot_assert_its_own_adoption() -> None:
    """Reference proof is not adoption (ADR-0006 § 5), and the evidence list was
    the hole that rule left open — `dotmac_starter_mt:main@93aecc80…` sat in the
    auth-oidc dossier and `dotmac_starter_mt:…-v0.2.0a2@55750e10…` in the
    deployment-foundation one."""
    bad = _typed_evidence("dotmac-ticketing", repository="dotmac_starter_mt")
    assert any(
        "cannot assert its own adoption" in problem
        for problem in _evidence_problems(bad, "dotmac-ticketing")
    )


def test_a_registry_path_cannot_sit_in_the_repository_slot() -> None:
    """Three vendor dossiers put `ghcr.io/...@sha256:...` where a repository name
    belongs, which is why no machine could read them."""
    bad = _typed_evidence(
        "dotmac-ticketing",
        repository="ghcr.io/michaelayoade/dotmac_vendor_control_plane",
    )
    assert any(
        "not a repository name" in problem
        for problem in _evidence_problems(bad, "dotmac-ticketing")
    )


def test_the_live_pin_is_pointed_at_and_never_copied() -> None:
    """The structural separation of history from the present tense.

    A `pinned_at` history with no `current_pin` pointer leaves a reader unable
    to tell the newest historical pin from the live one — which is exactly the
    confusion that put a twenty-minute-old value in a dossier. And a pointer
    that carries a value has simply become the copy again.
    """
    orphaned = _typed_evidence("dotmac-ticketing")
    del orphaned["adoption_evidence_pointer"]
    assert any(
        "must carry a `current_pin`" in problem
        for problem in _evidence_problems(orphaned, "dotmac-ticketing")
    )

    valued = _typed_evidence("dotmac-ticketing")
    valued["adoption_evidence_pointer"][0]["version"] = "0.1.0a1"
    assert any(
        "A pointer says WHERE" in problem
        for problem in _evidence_problems(valued, "dotmac-ticketing")
    )


def test_an_attestation_records_who_saw_it_and_when() -> None:
    """A run's logs expire. When the coordinate stops resolving the correct
    report is `unresolvable`, not `failed` — but only if the row degrades into a
    signed human attestation rather than into a dead link."""
    fragment = _typed_evidence("dotmac-ticketing")
    fragment["adoption_evidence"].append(
        {
            "kind": "image_digest",
            "repository": "dotmac_vendor_control_plane",
            "commit": _GOOD_COMMIT,
            "digest": "sha256:" + "a" * 64,
            "observed": "deployed",
            "observed_at": "2026-08-17",
            "observed_by": "michaelayoade",
        }
    )
    assert _evidence_problems(fragment, "dotmac-ticketing") == []

    for missing in ("observed", "observed_at", "observed_by"):
        undated = copy.deepcopy(fragment)
        del undated["adoption_evidence"][1][missing]
        assert _evidence_problems(undated, "dotmac-ticketing"), missing

    by_tag = copy.deepcopy(fragment)
    by_tag["adoption_evidence"][1]["digest"] = "v0.2.0a2"
    assert any(
        "image by TAG is not a coordinate" in problem
        for problem in _evidence_problems(by_tag, "dotmac-ticketing")
    )


def test_the_typed_shape_must_declare_its_version() -> None:
    """Two evidence shapes reachable under one key with no declared version is
    how a reader ends up guessing which contract they hold."""
    undeclared = _typed_evidence("dotmac-ticketing")
    del undeclared["adoption_evidence_schema"]
    assert any(
        "adoption_evidence_schema" in problem
        for problem in _evidence_problems(undeclared, "dotmac-ticketing")
    )


def test_every_dossier_carries_only_typed_evidence() -> None:
    """The corpus-wide statement, package level and slices together."""
    offenders: list[str] = []
    for package_dir in _shared_package_dirs():
        dossier = _load_toml(package_dir / "EXTRACTION.toml")
        offenders.extend(
            f"{package_dir.name}: {problem}"
            for problem in evidence_schema.free_text_problems(
                "", dossier.get("adoption_evidence")
            )
        )
        for entry in dossier.get("slices") or []:
            offenders.extend(
                f"{package_dir.name}: {problem}"
                for problem in evidence_schema.free_text_problems(
                    "", entry.get("adoption_evidence")
                )
            )
    assert offenders == [], offenders


def test_the_unmonitored_half_is_named_rather_than_implied() -> None:
    """ADR-0018 / rule 23: a region is unmonitored rather than exempt, and that
    has to be stated.

    This gate is offline shape only. It cannot establish PRESENT-TENSE
    consumption: a merged commit proves adoption happened and can never prove a
    consumer is current today. That needs a scheduled external re-derivation
    against the cited repositories, which is not built. Each entry below names
    what is not checked and what would have to exist to check it — and the
    seam is already in the data: every assertion row is a complete fetch
    instruction, every pointer a complete present-tense query.
    """
    required = {
        "present_tense_consumption",
        "assertion_resolution",
        "oracle_resolution",
        "in_place_edit_ratchet",
        "consumer_cross_check",
    }
    assert set(evidence_schema.UNMONITORED_BY_THIS_GATE) == required
    for name, reason in evidence_schema.UNMONITORED_BY_THIS_GATE.items():
        assert "NOT BUILT" in reason or "NOT enforced" in reason, name

    # And the seam is real: every migrated dossier that records a pin history
    # also records where the live answer is, so the unbuilt checker has
    # coordinates to run against rather than a note asking someone to look.
    for package_dir in _shared_package_dirs():
        dossier = _load_toml(package_dir / "EXTRACTION.toml")
        for scope in (dossier, *(dossier.get("slices") or [])):
            rows = scope.get("adoption_evidence") or []
            if not any(
                isinstance(row, dict) and row.get("kind") == "pinned_at" for row in rows
            ):
                continue
            pointers = scope.get("adoption_evidence_pointer") or []
            assert any(
                pointer.get("subject") == "current_pin" and pointer.get("paths")
                for pointer in pointers
            ), package_dir.name


def test_missing_product_test_proof_is_rejected() -> None:
    """Sensitivity proof: a plausible-looking dossier still fails without tests."""
    dossier = _load_toml(PACKAGES_DIR / "dotmac-template-studio/EXTRACTION.toml")
    dossier["preserved_tests"] = []

    with pytest.raises(ExtractionDossierError, match="preserved_tests"):
        _validate_dossier(
            dossier,
            directory_name="dotmac-template-studio",
            distribution_name="dotmac-template-studio",
        )


def test_a_new_package_cannot_hide_behind_audit_required() -> None:
    """Sensitivity proof: unresolved status is closed debt, not an entry mode."""
    dossier = _load_toml(PACKAGES_DIR / "dotmac-template-studio/EXTRACTION.toml")
    dossier["package"] = "dotmac-new-module"
    dossier["status"] = "audit-required"
    dossier["source_mode"] = "unresolved"

    with pytest.raises(ExtractionDossierError, match="PRE_RULE_DEBT"):
        _validate_dossier(
            dossier,
            directory_name="dotmac-new-module",
            distribution_name="dotmac-new-module",
        )


def test_reuse_proven_needs_two_contract_consumers() -> None:
    """Sensitivity proof: the top of the ladder still means what it says.

    ADR-0006's 2026-08-12 amendment lowered what a module needs to EXIST; it did
    not lower what "reuse is proven" claims.
    """
    dossier = _load_toml(PACKAGES_DIR / "dotmac-template-studio/EXTRACTION.toml")
    dossier.update(
        {
            "package": "dotmac-new-module",
            "status": "reuse-proven",
            "source_mode": "product-first",
            "contract_consumers": ["dotmac_erp"],
        }
    )

    with pytest.raises(ExtractionDossierError, match="exactly adopted"):
        _validate_dossier(
            dossier,
            directory_name="dotmac-new-module",
            distribution_name="dotmac-new-module",
        )


def test_the_ladder_climbs_when_the_second_consumer_arrives() -> None:
    """The ratchet upward.

    Two products on the contract make the reuse claim provable, and a package
    parked below its evidence carries an unearned understatement — the shape
    ADR-0018 rejects in the other direction.
    """
    dossier = _load_toml(PACKAGES_DIR / "dotmac-ticketing/EXTRACTION.toml")
    dossier["contract_consumers"] = ["dotmac_vendor_control_plane", "dotmac_sub"]

    with pytest.raises(ExtractionDossierError, match="exactly reuse-proven"):
        _validate_dossier(
            dossier,
            directory_name="dotmac-ticketing",
            distribution_name="dotmac-ticketing",
        )


def test_a_package_with_one_consumer_may_not_claim_nothing_adopted_it() -> None:
    """The defect the two-state model allowed, and the reason `adopted` exists.

    Under the old ladder a package could gain its first real consumer and keep
    describing itself as "nothing has adopted it yet" indefinitely — a FALSE
    statement about the fleet, not merely an incomplete one, and it concealed
    the first cutover that ADR-0017 calls the scarce resource.
    """
    dossier = _load_toml(PACKAGES_DIR / "dotmac-ticketing/EXTRACTION.toml")
    dossier["contract_consumers"] = ["dotmac_vendor_control_plane"]

    with pytest.raises(ExtractionDossierError, match="exactly adopted"):
        _validate_dossier(
            dossier,
            directory_name="dotmac-ticketing",
            distribution_name="dotmac-ticketing",
        )


def test_one_consumer_is_enough_to_be_a_shared_module() -> None:
    """The amendment's substance: a single consumer is evidence, not a veto.

    A vendor-side capability whose data-plane consumer is REVOKEd on purpose
    cannot acquire a second consumer until a second control plane exists.  The
    old gate did not delay such a module — it forbade it.
    """
    dossier = _load_toml(PACKAGES_DIR / "dotmac-ticketing/EXTRACTION.toml")
    dossier.update(
        {
            "status": "adopted",
            "candidate_consumers": ["dotmac_vendor_control_plane"],
            "contract_consumers": ["dotmac_vendor_control_plane"],
            # `adopted` now owes RE-CHECKABLE evidence. The fixture supplies it
            # rather than being exempted: a synthetic dossier that could skip
            # the rule would stop exercising the gate it exists to exercise.
            **_typed_evidence("dotmac-ticketing"),
        }
    )

    _validate_dossier(
        dossier,
        directory_name="dotmac-ticketing",
        distribution_name="dotmac-ticketing",
    )


def test_audit_complete_cannot_be_claimed_without_the_inventory() -> None:
    """Specificity: the status asserts the ERP/Sub audit happened.

    Without an audited `source_mode` it would just be `unresolved` wearing a
    better name — a new package's route around the gate.
    """
    dossier = _load_toml(PACKAGES_DIR / "dotmac-ticketing/EXTRACTION.toml")
    dossier["source_mode"] = "unresolved"

    with pytest.raises(ExtractionDossierError, match="inventory was done"):
        _validate_dossier(
            dossier,
            directory_name="dotmac-ticketing",
            distribution_name="dotmac-ticketing",
        )


def test_an_unadopted_module_must_name_at_least_one_candidate_consumer() -> None:
    """An unadopted package built for nobody is the speculative extraction §5 forbids.

    One CONCRETE candidate — an assembly that exists and will consume it — is
    the smallest claim that still carries evidence before adoption. Zero at
    this state is a package with no reason to be a package.
    """
    dossier = _load_toml(PACKAGES_DIR / "dotmac-ticketing/EXTRACTION.toml")
    dossier["candidate_consumers"] = []

    with pytest.raises(ExtractionDossierError, match="before adoption"):
        _validate_dossier(
            dossier,
            directory_name="dotmac-ticketing",
            distribution_name="dotmac-ticketing",
        )


def test_an_adopted_module_needs_no_invented_future_candidate() -> None:
    """The first real consumer replaces candidate demand with adoption evidence."""
    dossier = _load_toml(PACKAGES_DIR / "dotmac-ticketing/EXTRACTION.toml")
    dossier.update(
        {
            "status": "adopted",
            **_typed_evidence("dotmac-ticketing"),
            "contract_consumers": ["dotmac_vendor_control_plane"],
            "candidate_consumers": [],
        }
    )

    _validate_dossier(
        dossier,
        directory_name="dotmac-ticketing",
        distribution_name="dotmac-ticketing",
    )


def test_one_candidate_consumer_is_accepted() -> None:
    """The half of the previous rule that was actually wrong."""
    dossier = _load_toml(PACKAGES_DIR / "dotmac-ticketing/EXTRACTION.toml")
    dossier["candidate_consumers"] = ["dotmac_vendor_control_plane"]

    _validate_dossier(
        dossier,
        directory_name="dotmac-ticketing",
        distribution_name="dotmac-ticketing",
    )


# ---------------------------------------------------------------------------
# Sensitivity proofs for the schema-2 slice rules (ADR-0018: a gate with no
# proof it fires is indistinguishable from a blind one).
# ---------------------------------------------------------------------------


def _ui_dossier() -> dict[str, Any]:
    return _load_toml(PACKAGES_DIR / "dotmac-ui/EXTRACTION.toml")


def _validate_ui(dossier: dict[str, Any]) -> None:
    _validate_dossier(
        dossier, directory_name="dotmac-ui", distribution_name="dotmac-ui"
    )


def test_the_headline_cannot_claim_more_than_its_weakest_slice() -> None:
    """The whole point of slices: a strong contract must not carry a weak one."""
    dossier = _ui_dossier()
    components = next(s for s in dossier["slices"] if s["name"] == "components")
    components["contract_consumers"] = ["dotmac_erp"]
    components["status"] = "adopted"
    dossier["status"] = "reuse-proven"

    with pytest.raises(ExtractionDossierError, match="weakest contract slice"):
        _validate_ui(dossier)


def test_a_reference_consumer_cannot_be_counted_as_adoption() -> None:
    """Consumption by the owning assembly proves wiring, not independent choice."""
    dossier = _ui_dossier()
    components = next(s for s in dossier["slices"] if s["name"] == "components")
    components["contract_consumers"] = ["dotmac_starter_mt"]

    with pytest.raises(ExtractionDossierError) as excinfo:
        _validate_ui(dossier)
    assert "reference proof is not adoption" in str(excinfo.value)


def test_a_slice_cannot_claim_more_than_its_consumers_prove() -> None:
    dossier = _ui_dossier()
    components = next(s for s in dossier["slices"] if s["name"] == "components")
    components["contract_consumers"] = ["dotmac_erp"]
    components["status"] = "reuse-proven"

    with pytest.raises(ExtractionDossierError, match="evidence level is exactly"):
        _validate_ui(dossier)


def test_the_package_consumer_list_must_match_the_slices() -> None:
    """The summary is derived; it cannot drift from the rows beneath it."""
    dossier = _ui_dossier()
    dossier["contract_consumers"] = ["dotmac_sub", "dotmac_academy_app"]

    with pytest.raises(ExtractionDossierError, match="union of the slices"):
        _validate_ui(dossier)


def test_schema_2_requires_slices_and_schema_1_forbids_them() -> None:
    missing = _ui_dossier()
    del missing["slices"]
    with pytest.raises(ExtractionDossierError, match=r"requires a non-empty"):
        _validate_ui(missing)

    downgraded = _ui_dossier()
    downgraded["schema_version"] = 1
    with pytest.raises(ExtractionDossierError, match="require schema_version 2"):
        _validate_ui(downgraded)


def test_the_current_ui_dossier_passes_unmodified() -> None:
    """Sensitivity in the other direction: the rules above must not reject the
    real dossier, or every proof here would pass for the wrong reason."""
    _validate_ui(_ui_dossier())


def test_deleting_a_slice_cannot_restore_a_stronger_headline() -> None:
    """The escape hatch this binding closes.

    Drop the inconvenient `components` slice and the remaining rows validate
    happily, with the headline back at `reuse-proven`. The required slice names
    are probed from what the package actually PUBLISHES, so the dossier cannot
    disagree with the package about which contracts exist.
    """
    import dotmac_ui

    assert dotmac_ui.PUBLISHED_COMPONENT_CLASSES, (
        "this proof is only meaningful while a component class is published; "
        "if the library is withdrawn, the requirement correctly disappears"
    )

    dossier = _ui_dossier()
    dossier["slices"] = [s for s in dossier["slices"] if s["name"] != "components"]
    dossier["status"] = "reuse-proven"

    with pytest.raises(ExtractionDossierError, match="no slice"):
        _validate_ui(dossier)


def test_deleting_the_map_frame_slice_cannot_overstate_the_package() -> None:
    """Sensitivity proof for the new audit-complete contract.

    Without a live-surface binding, deleting only the weakest row and changing
    the headline back to reuse-proven would validate, even though MAP_FRAME is
    still published by the package.
    """
    import dotmac_ui

    assert dotmac_ui.MAP_FRAME in dotmac_ui.COMPONENTS

    dossier = _ui_dossier()
    dossier["slices"] = [
        entry for entry in dossier["slices"] if entry["name"] != "map-frame"
    ]
    dossier["status"] = "reuse-proven"

    with pytest.raises(ExtractionDossierError, match="no slice"):
        _validate_ui(dossier)


def test_a_slice_must_name_its_own_candidates() -> None:
    """Package-level candidates say nothing about a particular contract."""
    dossier = _ui_dossier()
    components = next(s for s in dossier["slices"] if s["name"] == "components")

    del components["candidate_consumers"]
    with pytest.raises(ExtractionDossierError, match="candidate_consumers"):
        _validate_ui(dossier)

    components["candidate_consumers"] = []
    with pytest.raises(ExtractionDossierError, match="at least one concrete"):
        _validate_ui(dossier)


def test_one_concrete_candidate_is_enough_for_a_slice() -> None:
    """The ADR floor is ONE, not two.

    ADR-0006's 2026-08-12 amendment lowers the candidate count to one
    deliberately -- "a dossier with no named candidate is a package built for
    nobody" -- and reserves TWO for `reuse-proven`, which counts CONTRACT
    consumers. A gate stricter than the decision it enforces sends people to
    the wrong document, so this pins the floor in the permissive direction.
    """
    dossier = _ui_dossier()
    components = next(s for s in dossier["slices"] if s["name"] == "components")
    components["candidate_consumers"] = ["dotmac_crm"]

    _validate_ui(dossier)


def test_a_reference_consumer_cannot_be_a_slice_candidate() -> None:
    """A candidate is a product that does NOT have the contract yet.

    The starter already consumes the component slice as reference proof, so
    naming it a candidate would let the same product satisfy both the "who is
    this for" and the "who proved the wiring" columns.
    """
    dossier = _ui_dossier()
    components = next(s for s in dossier["slices"] if s["name"] == "components")
    components["candidate_consumers"] = ["dotmac_starter_mt", "dotmac_erp"]

    with pytest.raises(ExtractionDossierError, match="not future demand"):
        _validate_ui(dossier)


def test_a_slice_cannot_name_an_existing_consumer_as_a_candidate() -> None:
    dossier = _ui_dossier()
    tokens = next(s for s in dossier["slices"] if s["name"] == "tokens")
    tokens["candidate_consumers"] = ["dotmac_erp", "dotmac_sub"]

    with pytest.raises(ExtractionDossierError, match="already"):
        _validate_ui(dossier)


def test_slice_evidence_references_are_checked_as_strictly_as_the_package() -> None:
    dossier = _ui_dossier()
    tokens = next(s for s in dossier["slices"] if s["name"] == "tokens")

    tokens["preserved_tests"] = ["not-a-reference"]
    with pytest.raises(ExtractionDossierError, match="repository:path"):
        _validate_ui(dossier)

    tokens["preserved_tests"] = ["dotmac_starter_mt:tests/unit/test_does_not_exist.py"]
    with pytest.raises(ExtractionDossierError, match="does not exist"):
        _validate_ui(dossier)


def test_schema_2_summary_prose_must_cover_every_slice() -> None:
    """The legacy top-level fields described ONE contract; the headline now
    describes the weakest, so the summary has to name them all."""
    dossier = _ui_dossier()
    dossier["contract"] = "Semantic tokens and compiled assets"

    with pytest.raises(ExtractionDossierError, match="does not mention"):
        _validate_ui(dossier)


# ── `stateless-protocol-adapter` semantics, and the proof they bite ─────────


def _synthetic_adapter(root: Path, *, body: str = "def go() -> None: ...\n") -> Path:
    package = root / "dotmac-fake-adapter"
    src = package / "src" / "dotmac_fake_adapter"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text(body, encoding="utf-8")
    return package


def test_a_conforming_stateless_adapter_has_no_violations(tmp_path: Path) -> None:
    """The negative control. Without it, every assertion below could pass
    because the checker returns problems for anything at all."""
    assert stateless_package_violations(_synthetic_adapter(tmp_path)) == []


def test_the_real_adapter_conforms() -> None:
    """`dotmac-auth-oidc` is the first package to claim the classification, so
    it is also the first thing the rule is measured against."""
    package = PACKAGES_DIR / "dotmac-auth-oidc"
    if not package.is_dir():  # pragma: no cover - package not yet present
        pytest.skip("dotmac-auth-oidc is not in this tree")
    assert stateless_package_violations(package) == []


def test_a_planted_manifest_is_caught(tmp_path: Path) -> None:
    package = _synthetic_adapter(
        tmp_path, body="from dotmac_kernel.modules import ModuleManifest\n"
    )
    problems = stateless_package_violations(package)
    assert any("ModuleManifest" in problem for problem in problems), problems


@pytest.mark.parametrize("declaration", ["short_code", "migration_prefix"])
def test_a_planted_lineage_declaration_is_caught(
    tmp_path: Path, declaration: str
) -> None:
    package = _synthetic_adapter(tmp_path, body=f'{declaration} = "oid"\n')
    problems = stateless_package_violations(package)
    assert any(declaration in problem for problem in problems), problems


def test_a_planted_migrations_tree_is_caught(tmp_path: Path) -> None:
    package = _synthetic_adapter(tmp_path)
    (package / "src" / "dotmac_fake_adapter" / "migrations").mkdir()
    problems = stateless_package_violations(package)
    assert any("migrations" in problem for problem in problems), problems


@pytest.mark.parametrize("root", ["sqlalchemy", "alembic", "psycopg"])
def test_a_planted_persistence_import_is_caught(tmp_path: Path, root: str) -> None:
    """The property that would otherwise rot silently: a package that grew an
    ORM has become a module without its dossier changing."""
    package = _synthetic_adapter(tmp_path, body=f"import {root}\n")
    problems = stateless_package_violations(package)
    assert any(root in problem for problem in problems), problems


def test_prose_explaining_the_absence_does_not_trip_the_rule(tmp_path: Path) -> None:
    """The complement, and it is not hypothetical: the FIRST version of this
    checker used substring matching and failed `dotmac-auth-oidc`, whose
    `__init__` docstring says it declares no `short_code`/`migration_prefix`
    precisely so a reader knows it is stateless. A guard that punishes its own
    documentation trains people to delete the documentation."""
    package = _synthetic_adapter(
        tmp_path,
        body=(
            '"""Declares no short_code, no migration_prefix, no ModuleManifest,\n'
            'and imports no sqlalchemy — deliberately."""\n'
            "# import sqlalchemy\n"
        ),
    )
    assert stateless_package_violations(package) == []


def test_the_classification_is_not_applied_to_other_packages(tmp_path: Path) -> None:
    """Keyed on the DECLARED classification: a stateful module must be out of
    scope, not accidentally held to a rule it is supposed to fail."""
    dossier = _load_toml(PACKAGES_DIR / "dotmac-ticketing/EXTRACTION.toml")
    assert dossier["classification"] != STATELESS_ADAPTER
    # Ticketing WOULD fail the rule — it has a manifest and a lineage — and the
    # sweep passes today, which is the evidence that the check is scoped.
    assert stateless_package_violations(PACKAGES_DIR / "dotmac-ticketing")


# --- `stateless-contract-catalogue`: the fifth property -----------------------
#
# ADR-0018 requires a guard to be shown biting. The four shared properties are
# already proved above, so what needs proving here is only the property that
# earns the separate word — and, just as importantly, that it does NOT reach
# the classification it was carved out of.


def test_a_conforming_contract_catalogue_has_no_violations(tmp_path: Path) -> None:
    """The negative control for the five-property checker."""
    package = _synthetic_adapter(
        tmp_path, body="CAPABILITY_SCHEMAS: tuple[bytes, ...] = ()\n"
    )
    assert contract_catalogue_violations(package) == []


@pytest.mark.parametrize("root", sorted(NETWORK_ROOTS))
def test_a_catalogue_that_reaches_a_provider_is_caught(
    tmp_path: Path, root: str
) -> None:
    package = _synthetic_adapter(tmp_path, body=f"import {root}\n")
    problems = contract_catalogue_violations(package)
    assert any(root in problem for problem in problems), problems


@pytest.mark.parametrize("root", sorted(NETWORK_ROOTS))
def test_the_same_import_is_fine_in_an_ADAPTER(tmp_path: Path, root: str) -> None:
    """The discriminator, stated as a test.

    If this passed for both classifications the fifth property would be
    decoration and the connector lane's "profile, not a classification" ruling
    would apply unchanged. It is the ONE place the two words are allowed to
    disagree, so it is the one that has to be pinned.
    """
    package = _synthetic_adapter(tmp_path, body=f"import {root}\n")
    assert stateless_package_violations(package) == []


def test_a_real_adapter_is_refused_by_the_catalogue_checker() -> None:
    """A synthetic proof can be tuned until it passes; a real package cannot.

    `dotmac-auth-oidc` satisfies the adapter classification exactly — the sweep
    above asserts it — and must STILL be refused here, because it ships
    `transport.py` and declares `httpx`. That is what stops the catalogue
    classification from becoming a second spelling of the adapter one.
    """
    package = PACKAGES_DIR / "dotmac-auth-oidc"
    if not package.is_dir():  # pragma: no cover - package not yet present
        pytest.skip("dotmac-auth-oidc is not in this tree")
    assert stateless_package_violations(package) == []
    assert contract_catalogue_violations(package), (
        "the real adapter passed the catalogue checker — the fifth property is "
        "not being enforced"
    )


def test_no_package_claims_the_catalogue_classification_yet() -> None:
    """A check over an empty set passes for the wrong reason.

    No `stateless-contract-catalogue` package exists on main: the seven
    candidates are archive-only and floor on a kernel grammar that is not
    published. This test records that emptiness DELIBERATELY, so the first real
    catalogue to land flips it and the reviewer is made to look at the sweep
    rather than inheriting a green that never measured anything.
    """
    claimed = [
        package.name
        for package in sorted(PACKAGES_DIR.iterdir())
        if (package / "EXTRACTION.toml").is_file()
        and _load_toml(package / "EXTRACTION.toml").get("classification")
        == CONTRACT_CATALOGUE
    ]
    assert claimed == [], (
        f"{claimed} now claim the catalogue classification — delete this test "
        "and assert the real sweep instead"
    )
