"""`dotmac_kernel.__version__` must equal the distribution version.

The kernel carries its version in TWO places that nothing kept in sync:
`packages/dotmac-kernel/pyproject.toml`'s `[tool.poetry] version` (what the
wheel is published as) and the `__version__` literal in
`dotmac_kernel/__init__.py` (what a consumer reads at runtime).

Caught during the 0.1.0a9 release prep: the pyproject bump landed, the literal
did not, and the floor-proof probe cheerfully printed ``kernel 0.1.0a8`` while
exercising a wheel built as ``0.1.0a9``. Every gate still passed, because no
test compared them.

That drift is worse than cosmetic. `__version__` is the value a consumer logs,
reports in a support bundle, or branches on when working around a known kernel
defect — so a stale literal makes a deployment lie about which kernel it is
running, and the lie survives exactly as long as nobody diffs two files by
hand.

Deliberately compares against the pyproject SOURCE rather than
`importlib.metadata.version`: in an editable install the installed metadata can
itself be stale, so asserting against it would let both values be wrong
together — which is the failure this test exists to catch.
"""

from __future__ import annotations

import importlib.util
import itertools
import json
import re
import tomllib
from pathlib import Path

import dotmac_kernel
import pytest

PACKAGES = Path(__file__).resolve().parents[2] / "packages"
_KERNEL = PACKAGES / "dotmac-kernel"
_PYPROJECT = _KERNEL / "pyproject.toml"


def _declared_version() -> str:
    data = tomllib.loads(_PYPROJECT.read_text())
    return data["tool"]["poetry"]["version"]


def test_runtime_version_matches_the_distribution_version() -> None:
    declared = _declared_version()
    assert dotmac_kernel.__version__ == declared, (
        f"dotmac_kernel.__version__ is {dotmac_kernel.__version__!r} but "
        f"pyproject declares {declared!r} — bump BOTH: "
        "packages/dotmac-kernel/pyproject.toml and "
        "packages/dotmac-kernel/src/dotmac_kernel/__init__.py"
    )


def test_the_version_is_a_pep440_release_or_prerelease() -> None:
    """A malformed version fails the publish late, after the build job has
    already run; catching the shape here keeps that failure local."""
    assert re.fullmatch(
        r"\d+\.\d+\.\d+(?:a|b|rc)?\d*", dotmac_kernel.__version__
    ), f"not a PEP 440 version: {dotmac_kernel.__version__!r}"


# ── A module's floor is the kernel release that allocated its schema ─────────

# Each installable module's `dotmac-kernel` floor, and the CHANGELOG heading that
# must document why that release exists. A module cannot be registered by a
# kernel predating its `MIGRATION_OWNER_LEDGER` row — `NamespaceRegistry
# .from_manifests` raises `UnallocatedNamespaceError` — so the floor is not a
# preference and drifting it produces a runtime boot failure, not a warning.
#
# The exact numbers matter beyond correctness: a42 and a43 belong to the
# upstream train (Audit R1, Application Directory / Workspace). Two branches
# minting one kernel version collide in the changelog and leave a consumer
# unable to say which a42 it pinned, so the vendor modules renumbered to a44/a45
# rather than the foundations renumbering around them.
#
# People consumes no kernel feature newer than its allocation release. If it
# adopts a newer capability, move its row to CAPABILITY_RAISED_FLOORS while
# retaining the allocation release as evidence there. Durable timers was
# allocated in a72. Immutable a73 belongs to the caller-session transaction
# release, a74-a77 to the vendor cohort, and media observations, content,
# publishing and sites therefore follow in a78, a79, a80 and a81.
LEDGER_ALLOCATION_RELEASES: dict[str, str] = {
    "dotmac-people": "0.1.0a71",
    # ADR-0026 allocated `mod_approvals` in a59; the corrected explicit
    # plane-selection contract lands in a61, so its row lives in
    # CAPABILITY_RAISED_FLOORS below rather than here.
    # Durable timers consumes a67's relay contract, but its own namespace is
    # allocated later, so the allocation remains the effective floor.
    "dotmac-durable-timers": "0.1.0a72",
    # Brand profiles is the ordinary case for a fourth time: `BRAND_PROFILES_
    # MIGRATION_OWNER` and this module landed in the same a77 change, and every
    # capability it consumes — `platform_tables` (a53), the prerequisite
    # contract (a56), `supported_plane_sets` (a61) — predates that allocation.
    # Its three siblings from the same ADR-0033 cohort do NOT sit here; see
    # UNPUBLISHED_ALLOCATION_FLOORS below for why an allocation can fail to be
    # a floor.
    "dotmac-brand-profiles": "0.1.0a77",
    # Sites is the marketing cohort tip: a81 both allocates its lineage and is
    # the first installable kernel carrying all four marketing allocations.
    "dotmac-sites": "0.1.0a81",
    # The network cohort: eleven allocations minted together in a82, and the
    # ordinary case for all eleven. Every capability each one consumes —
    # `requires` (a56), `tenant_requires` (a60), the prerequisite names
    # `tenant_scope_catalog.v1` and `module_database_roles.v1` — predates the
    # allocation, so the ledger row alone sets the floor. They share ONE kernel
    # version deliberately: stacking eleven bumps would mint ten numbers no
    # installer could resolve, which is the a74..a76 / a78..a80 history above.
    "dotmac-inventory": "0.1.0a82",
    "dotmac-assets": "0.1.0a82",
    "dotmac-ipam": "0.1.0a82",
    "dotmac-network-inventory": "0.1.0a82",
    "dotmac-network-observability": "0.1.0a82",
    "dotmac-network-topology": "0.1.0a82",
    "dotmac-network-assurance": "0.1.0a82",
    "dotmac-network-control": "0.1.0a82",
    "dotmac-fiber-plant": "0.1.0a82",
    "dotmac-network-access": "0.1.0a82",
    "dotmac-pon-access": "0.1.0a82",
    # Positioning is the ordinary case once more: a83 allocates `pos`/`po` and
    # every capability it consumes — `requires` (a56), the two prerequisite
    # names — predates that allocation, so the ledger row alone sets the floor.
    "dotmac-positioning": "0.1.0a83",
}

# The exceptions: a module whose floor is set by a kernel CAPABILITY it consumes
# rather than by its own ledger row, because that capability landed later. The
# floor is always the HIGHER of the two — a kernel that cannot import the
# manifest never reaches the allocation check.
#
# Kept as a separate, reasoned map rather than by quietly omitting the module
# from the one above: an unlisted module is an untested floor, and "this one is
# special" has to say why.
CAPABILITY_RAISED_FLOORS = {
    # Immutable tag inspection is the evidence here: a71's changelog described
    # the campaign allocation early, but CAMPAIGNS_MIGRATION_OWNER first exists
    # in published tag a72. a73 is the operative floor because Sub-first
    # adoption invokes consent, idempotency and delivery with Sub's assembly-
    # owned Session; before a73 those services imported dotmac_kernel.db and
    # constructed a second engine/session runtime.
    "dotmac-campaigns": ("0.1.0a73", "0.1.0a72"),
    # ADR-0006 D1 amendment: every lineage below declares `ModuleManifest
    # .requires` and its root calls `resolve_depends_on` /
    # `require_prerequisites`. All three arrived in a56, so a kernel below that
    # cannot import the manifest, let alone run the migration — which is why
    # these moved out of LEDGER_ALLOCATION_RELEASES rather than keeping their
    # allocation floors.
    "dotmac-application-directory": ("0.1.0a56", "0.1.0a46"),
    # a56 made fi_0001's prerequisite declarations installable; a61 is now the
    # operative floor because a3 declares supported_plane_sets and fi_0002
    # consumes selected_module_planes.
    "dotmac-files": ("0.1.0a61", "0.1.0a54"),
    "dotmac-imports": ("0.1.0a56", "0.1.0a55"),
    "dotmac-template-studio": ("0.1.0a56", "0.1.0a13"),
    # ADR-0023 dual-plane (`platform_tables`, a53) raised this one first; the
    # prerequisite contract raises it again. The floor is always the highest
    # capability the module actually consumes, not the first one that moved it.
    # ADR-0028: both modules declare `supported_plane_sets`, added in a61, and
    # their lineages consume `selected_module_planes`. An earlier kernel raises
    # TypeError at manifest import, before the allocation check is ever reached,
    # so the capability outranks each module's own allocation.
    #
    # Ticketing passed through a56 (`requires`) and a60 (`tenant_requires`) on
    # the way here. The floor is always the highest capability the module
    # actually consumes, not the first one that moved it.
    # ADR-0023: release-catalog declares `platform_tables` and owns no tenant
    # tables. The field was INTRODUCED in a53, but a53 was never published —
    # the tags jump a50 to a56 — so a56 is the earliest kernel a consumer can
    # actually install with it. A floor naming an unpublished version is
    # unresolvable, which is why "earliest PUBLISHED" is the operative test,
    # not "earliest".
    #
    # It declares no `requires` and calls no prerequisite helper, so a56 here
    # is set by `platform_tables` alone and not by the a56 prerequisite
    # contract that raised the modules above. `dotmac-entitlement-allocation`
    # sat beside it on exactly that reasoning until `0.1.0a5`; see its own
    # entry below for why it no longer does.
    #
    # `supported_plane_sets` is deliberately OMITTED rather than written as an
    # explicit `()`. Writing it would consume an a61 constructor field for a
    # value the default already supplies, raising the floor to a61 for
    # nothing. Absence already means atomic.
    "dotmac-release-catalog": ("0.1.0a56", "0.1.0a44"),
    "dotmac-ticketing": ("0.1.0a61", "0.1.0a39"),
    # a61 (`supported_plane_sets`) held until a67 published `outbox_relay.v1`,
    # which this module's `ap_0002` verifies and its manifest declares. The
    # floor is always the HIGHEST capability actually consumed.
    "dotmac-approvals": ("0.1.0a67", "0.1.0a59"),
    # Numbering's own ledger row is a65, and for one release that WAS its floor
    # — every other capability it consumes (`platform_tables` a53,
    # `requires`/`tenant_requires` a56/a60, `supported_plane_sets` a61) predates
    # the allocation. a66 published `idempotency_ledger.v1`, the name for the
    # at-most-once tables `allocate` writes at request time. A kernel below a66
    # HAS those tables — `0018` created them — but does not know the name, so
    # `ModuleManifest.__post_init__` -> `validate_prerequisites` raises
    # `UnknownPrerequisiteError` at manifest import, before the allocation check
    # is ever reached. The floor moved for a capability, so the row moved with
    # it.
    "dotmac-numbering": ("0.1.0a66", "0.1.0a65"),
    # Integration held a58 — its own ledger row — through `0.1.0a1`, `0.1.0a2`
    # and `0.1.0a3`, all PUBLISHED. `0.1.0a4` declares `idempotency_ledger.v1`,
    # the a66 name for the at-most-once tables `run_effect_once` has been
    # writing at request time since a1, and verifies it in `ig_0007`. The
    # tables themselves are far older than the floor: kernel `0018` created
    # them (ADR-0014). What a65 and below lack is the NAME — `validate_
    # prerequisites` raises `UnknownPrerequisiteError` while constructing the
    # manifest, so the module cannot be imported at all, let alone reach the
    # a58 allocation check. a6 then declares `platform_audit_log.v1`, published
    # in a68, and verifies it in `ig_0008`. Capability outranks allocation, as
    # everywhere else in this map; the highest named effect sets the floor.
    #
    # Unlike numbering, whose a1 was never published, this floor raise is
    # visible to consumers: `dotmac_integrator` can pin any of three released
    # versions that run on a58..a65, and a4 will not. a6 additionally excludes
    # a66/a67 because neither knows the audit effect. That is the correct trade
    # — every one of those installs on a kernel whose ledger it silently
    # requires and cannot state.
    "dotmac-integration": ("0.1.0a68", "0.1.0a58"),
    # Entitlement allocation held a56 through `0.1.0a1`..`0.1.0a4`, all four
    # PUBLISHED, and that a56 was `platform_tables` (ADR-0023) rather than its
    # a45 ledger row. `0.1.0a5` declares `idempotency_ledger.v1`, the a66 name
    # for the at-most-once tables `stage_allocation` has been writing at
    # request time since a1, and verifies it in `ea_0002`. a6 also declares the
    # platform audit effect and verifies it in `ea_0003`; a68 is the first
    # kernel release that names that second effect. The tables are far
    # older than the floor — kernel `0018` created them (ADR-0014). What a65
    # and below lack is the NAME: `validate_prerequisites` raises
    # `UnknownPrerequisiteError` while the manifest is being constructed, so
    # the module cannot be imported at all, let alone reach the a45 allocation
    # check. Capability outranks allocation, as everywhere else in this map,
    # and the highest capability consumed is what the floor names — which is
    # why this row moved off a56 rather than staying beside release-catalog.
    # Same visible break as integration's, and accepted for the same reason:
    # four released versions install on a56..a65, a5 will not, and a6 also
    # excludes a66/a67. Every one
    # of those four runs against a kernel whose ledger it silently requires
    # and cannot state.
    "dotmac-entitlement-allocation": ("0.1.0a68", "0.1.0a45"),
}

# The third rule, and the one the other two maps cannot state: a module whose
# own allocation IS the highest thing it needs, in a kernel release that was
# never published.
#
# `dotmac-release-catalog`'s entry above already records the operative test —
# "earliest PUBLISHED", not "earliest" — but it applies it to a CAPABILITY
# (`platform_tables`, source a53, first installable a56). These three apply it
# to the allocation itself. Putting them in CAPABILITY_RAISED_FLOORS would
# assert a capability raised them and none did; putting them in
# LEDGER_ALLOCATION_RELEASES would assert `floor == allocation` and pin three
# versions no installer can resolve. So they get a map that says the true
# reason, which is this file's standing preference over a stretched one.
#
# The ADR-0033 cohort was built as four stacked pull requests, each bumping the
# kernel to carry its own ledger row: a74 (`cg`), a75 (`li`), a76 (`dc`), a77
# (`bp`). Only the tip was releasable — a kernel release publishes ONE version,
# and a74..a76 exist as changelog history rather than as artifacts, exactly as
# a53..a55 do. a77 is therefore the first installable kernel carrying any of
# these four allocations, which makes it the floor of all four rather than of
# the last one.
#
# The marketing cohort follows the same rule: a78 (`mo`), a79 (`ct`), a80
# (`pb`) and a81 (`si`). a78..a80 are allocation history, while a81 is the first
# kernel artifact that can load any member. Sites sits in the ordinary map
# because its allocation equals that floor; the first three sit here because
# their source allocations are necessarily unpublished.
#
# Each value is (floor, allocation). Unlike the map above, the gap is not a
# consumer break: no released version of these modules ever floored at a74..a76,
# because none of them has been released at all.
UNPUBLISHED_ALLOCATION_FLOORS = {
    "dotmac-commercial-agreements": ("0.1.0a77", "0.1.0a74"),
    "dotmac-licensing": ("0.1.0a77", "0.1.0a75"),
    "dotmac-deployment-control": ("0.1.0a77", "0.1.0a76"),
    "dotmac-media-observations": ("0.1.0a81", "0.1.0a78"),
    "dotmac-content": ("0.1.0a81", "0.1.0a79"),
    "dotmac-publishing": ("0.1.0a81", "0.1.0a80"),
}


def _alpha(release: str) -> int:
    """The alpha serial from `0.1.0aNN`.

    Compared as an int, not as a string: `"0.1.0a9" > "0.1.0a53"` is lexically
    true and numerically false, and this whole train is `0.1.0a*`.
    """
    match = re.fullmatch(r"0\.1\.0a(\d+)", release)
    assert match, f"unexpected release form {release!r}"
    return int(match.group(1))


def _kernel_changelog() -> str:
    return (PACKAGES / "dotmac-kernel" / "CHANGELOG.md").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("distribution", "release"), sorted(LEDGER_ALLOCATION_RELEASES.items())
)
def test_module_floor_is_the_release_that_allocated_its_schema(
    distribution: str, release: str
) -> None:
    manifest = tomllib.loads(
        (PACKAGES / distribution / "pyproject.toml").read_text(encoding="utf-8")
    )
    floor = manifest["tool"]["poetry"]["dependencies"]["dotmac-kernel"]
    assert floor == f">={release}", (
        f"{distribution} pins {floor!r}; its ledger row landed in {release}, and "
        "an earlier kernel cannot register the module at all"
    )


@pytest.mark.parametrize(
    ("distribution", "floors"), sorted(CAPABILITY_RAISED_FLOORS.items())
)
def test_a_capability_raised_floor_is_above_its_allocation(
    distribution: str, floors: tuple[str, str]
) -> None:
    """The exception, pinned so it stays deliberate.

    A module consuming a kernel capability newer than its namespace must pin the
    capability's release. Pinning the allocation instead would install a kernel
    that cannot import the manifest at all — a `TypeError` at boot, not a
    degraded feature.
    """
    floor, allocation = floors
    manifest = tomllib.loads(
        (PACKAGES / distribution / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert manifest["tool"]["poetry"]["dependencies"]["dotmac-kernel"] == f">={floor}"
    assert _alpha(floor) > _alpha(allocation), (
        f"{distribution} is listed as capability-raised but its floor {floor} is "
        f"not above its allocation {allocation} — move it to "
        "LEDGER_ALLOCATION_RELEASES"
    )


def _kernel_tag_serials() -> set[int]:
    """Every PUBLISHED kernel alpha serial, read from immutable tags.

    Reuses the publication sweep's tag reader rather than a second
    `git tag` of its own, so "what is published" has one answer in this
    repository. A refusal is a FAILURE, never a skip: `actions/checkout`
    fetches no tags by default, and a tag-blind version of this test would
    report green while comparing an empty set against everything — the exact
    hole `test_declared_publication.py` documents having had.
    """
    spec = importlib.util.spec_from_file_location(
        "declared_publication_sweep",
        PACKAGES.parent / "scripts" / "declared_publication_sweep.py",
    )
    assert spec is not None and spec.loader is not None
    sweep = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sweep)
    tags = sweep.git_tags()
    serials = {
        int(match.group(1))
        for tag in tags
        if (match := re.fullmatch(r"dotmac-kernel-v0\.1\.0a(\d+)", tag))
    }
    assert serials, (
        "no dotmac-kernel-v0.1.0a* tag is visible, so this test would pass by "
        "seeing nothing. Fetch tags (`git fetch --tags`) — a tag-blind run is "
        "a hole, not a pass"
    )
    return serials


@pytest.mark.parametrize(
    ("distribution", "floors"), sorted(UNPUBLISHED_ALLOCATION_FLOORS.items())
)
def test_an_unpublished_allocation_rounds_up_to_the_first_installable_kernel(
    distribution: str, floors: tuple[str, str]
) -> None:
    """The third rule, proven against tags rather than against prose.

    Two halves, and the second is the one that matters. The first says the
    module pins the floor. The second says the floor is honestly derived: NO
    kernel was ever published in `[allocation, floor)`, so rounding up was
    forced rather than chosen. Without it the map would accept any floor above
    the allocation, which is how a module quietly acquires a floor higher than
    it needs and stops installing on kernels that would have run it perfectly.
    """
    floor, allocation = floors
    manifest = tomllib.loads(
        (PACKAGES / distribution / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert manifest["tool"]["poetry"]["dependencies"]["dotmac-kernel"] == f">={floor}"
    assert _alpha(floor) > _alpha(allocation), (
        f"{distribution}'s floor {floor} is not above its allocation "
        f"{allocation}; if the allocation IS installable this row belongs in "
        "LEDGER_ALLOCATION_RELEASES"
    )
    lower, upper = _alpha(allocation), _alpha(floor)
    skipped = sorted(s for s in _kernel_tag_serials() if lower <= s < upper)
    assert not skipped, (
        f"{distribution} floors at {floor} but kernel 0.1.0a{skipped[0]} is "
        f"published and already carries the {allocation} allocation — the floor "
        "must name the FIRST installable kernel, not a later one"
    )


def test_a_module_has_exactly_one_floor_rule() -> None:
    """The maps must not overlap, or a module's floor has more than one answer.

    Checked pairwise rather than by summing three lengths against a set: a
    module listed in all three would leave the totals wrong in a way that is
    obvious, but one listed in two of three is the realistic mistake and the
    pairwise form names which two.
    """
    maps = {
        "LEDGER_ALLOCATION_RELEASES": set(LEDGER_ALLOCATION_RELEASES),
        "CAPABILITY_RAISED_FLOORS": set(CAPABILITY_RAISED_FLOORS),
        "UNPUBLISHED_ALLOCATION_FLOORS": set(UNPUBLISHED_ALLOCATION_FLOORS),
    }
    for left, right in itertools.combinations(sorted(maps), 2):
        assert not maps[left] & maps[right], (
            f"{sorted(maps[left] & maps[right])} is in both {left} and {right}; "
            "a module's floor must have exactly one rule"
        )


def test_every_releasable_module_has_a_floor_rule() -> None:
    """The other half of "exactly one" — and what keeps an EMPTY map watched.

    The map has been empty before, so this does not trust parametrization alone:
    it reads the release allowlist — the closed set of things that may be
    PUBLISHED — and requires every member to appear in exactly one floor map.
    Discovery, not enumeration: adding an allowlist entry enrols it here, the
    way `test_module_version_sync.py` already does for versions.

    The union may legitimately be a superset. `dotmac-imports` and
    `dotmac-template-studio` carry floors while still unpublishable, which is
    the correct direction — an untested floor is the defect, a tested one on an
    unreleased module is just early.
    """
    allowlist = set(
        json.loads(
            (PACKAGES.parent / ".github/release-modules.json").read_text("utf-8")
        )["modules"]
    )
    assert allowlist, "the allowlist is empty; this gate would prove nothing"
    unruled = sorted(
        allowlist
        - set(LEDGER_ALLOCATION_RELEASES)
        - set(CAPABILITY_RAISED_FLOORS)
        - set(UNPUBLISHED_ALLOCATION_FLOORS)
    )
    assert not unruled, (
        f"releasable module(s) {unruled} have no floor rule — a module absent "
        "from both maps has an untested kernel floor, and nothing would notice "
        "it drifting"
    )


@pytest.mark.parametrize(
    "release",
    sorted(
        {*LEDGER_ALLOCATION_RELEASES.values()}
        | {floor for floor, _ in CAPABILITY_RAISED_FLOORS.values()}
        | {floor for floor, _ in UNPUBLISHED_ALLOCATION_FLOORS.values()}
    ),
)
def test_each_allocation_release_is_documented(release: str) -> None:
    """A floor pointing at a release the changelog never explains is a version
    number nobody can audit."""
    assert f"## {release} —" in _kernel_changelog()


def test_no_vendor_module_claims_an_upstream_train_version() -> None:
    """a42 and a43 are spoken for. This test is the reason the renumber cannot
    silently come back: it fails if any vendor module's floor lands on one.

    Reads BOTH maps. It read only the allocation map until that map emptied,
    at which point it was asserting the empty set against a constant — green,
    and blind to every floor the fleet actually pins.
    """
    contested = {"0.1.0a42", "0.1.0a43"}
    floors = {
        *LEDGER_ALLOCATION_RELEASES.values(),
        *(floor for floor, _ in CAPABILITY_RAISED_FLOORS.values()),
        *(allocation for _, allocation in CAPABILITY_RAISED_FLOORS.values()),
        *(floor for floor, _ in UNPUBLISHED_ALLOCATION_FLOORS.values()),
        *(allocation for _, allocation in UNPUBLISHED_ALLOCATION_FLOORS.values()),
    }
    assert floors, "no floors to check; this gate would pass for the wrong reason"
    assert floors & contested == set()


def test_the_kernel_is_at_least_every_module_floor() -> None:
    """The composed assembly ships ONE kernel, so it must satisfy every module
    installed beside it — otherwise the composition cannot boot."""
    declared = tomllib.loads(
        (PACKAGES / "dotmac-kernel" / "pyproject.toml").read_text(encoding="utf-8")
    )["tool"]["poetry"]["version"]
    floors = {
        *LEDGER_ALLOCATION_RELEASES.values(),
        # The capability-raised floors belong here too, and are the ones most
        # likely to outrun the kernel: they move when a module adopts a NEW
        # kernel feature, not once when its namespace is allocated.
        *(floor for floor, _ in CAPABILITY_RAISED_FLOORS.values()),
        # These cannot outrun the kernel by construction — an unpublished
        # allocation rounds UP to a published release, and the kernel is at
        # least every release it has ever cut — but reading all three maps is
        # what stops the assertion going quiet if a map is renamed or emptied.
        *(floor for floor, _ in UNPUBLISHED_ALLOCATION_FLOORS.values()),
    }
    for release in floors:
        assert _alpha(declared) >= _alpha(
            release
        ), f"kernel is {declared} but a module floors at {release}"
