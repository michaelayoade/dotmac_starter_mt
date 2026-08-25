"""The inbox module's structural contract.

What this file protects is the property that makes the module shareable at all:
**no channel name appears in a conditional anywhere in the package.** Both source
products violate that constantly — Sub with a five-name frozenset, CRM with a
three-name literal list inside a partial unique index predicate — and every one
of those sets is a transport property nobody named. Everything else here guards
the mechanics that keep the property true: the trait vocabulary, the ledger
allocation, the schema qualification, and the absence of product-domain columns.

Behaviour lives in `tests/unit/test_inbox_channels.py`,
`test_inbox_threading.py` and `test_inbox_lifecycle.py`, in keeping with the
repo's static/behavioural split.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest
from dotmac_inbox import lifecycle, models, threading
from dotmac_inbox.manifest import module
from dotmac_kernel.namespaces import MIGRATION_OWNER_LEDGER, module_schema

MODULE_ROOT = Path(inspect.getfile(lifecycle)).parent
MIGRATIONS = MODULE_ROOT / "migrations" / "versions"

#: Channel names that appear in the fleet's two implementations. If any of these
#: turns up in a conditional in this package, the trait mechanism has been
#: bypassed and the module has started learning products' vocabularies.
KNOWN_CHANNEL_NAMES = frozenset(
    {
        "email",
        "whatsapp",
        "facebook_messenger",
        "instagram_dm",
        "facebook_comment",
        "instagram_comment",
        "chat_widget",
        "website_fiber",
        "field_job",
        "sms",
        "note",
    }
)


def _package_sources() -> list[Path]:
    return sorted(
        path
        for path in MODULE_ROOT.rglob("*.py")
        if "migrations" not in path.parts  # migrations are frozen artifacts
    )


def _channel_names_in_conditionals(source: str) -> set[tuple[str, int]]:
    """Every known channel name used as a literal inside a branch.

    `ast.walk` yields nested nodes as well as the enclosing branch, so a single
    `if c == 'whatsapp'` is reachable through both the `If` and its `Compare`.
    Returning a set is what keeps one occurrence from being reported twice.
    """
    tree = ast.parse(source)
    return {
        (inner.value, inner.lineno)
        for node in ast.walk(tree)
        if isinstance(node, ast.If | ast.IfExp | ast.Compare | ast.Match)
        for inner in ast.walk(node)
        if isinstance(inner, ast.Constant)
        and isinstance(inner.value, str)
        and inner.value in KNOWN_CHANNEL_NAMES
    }


# ── The property the whole design rests on ───────────────────────────────────


def test_no_channel_name_appears_in_a_conditional_in_the_package() -> None:
    """The core branches on TRAITS, never on a channel's identity.

    The sensitivity proof for this detector is
    `test_the_channel_name_detector_actually_fires`: it is easy to write a
    "no bad strings" test that passes because it is looking in the wrong place.

    Docstrings and comments are exempt — the module documents the channels it
    was designed against, and must, or the reasoning is unreviewable. Only
    executable comparisons count.
    """
    offenders = sorted(
        {
            f"{path.relative_to(MODULE_ROOT)}:{name!r} on line {line}"
            for path in _package_sources()
            for name, line in _channel_names_in_conditionals(
                path.read_text(encoding="utf-8")
            )
        }
    )
    assert not offenders, (
        "the core must branch on channel TRAITS, never on a channel's name — "
        "add or read a trait on ChannelSpec instead:\n  " + "\n  ".join(offenders)
    )


def test_the_channel_name_detector_actually_fires() -> None:
    """Sensitivity proof (ADR-0018): the guard above is not vacuous.

    Without this, a refactor that moved the sources or broke the AST walk would
    leave a permanently-green test asserting nothing.
    """
    branching = "def f(c):\n    if c == 'whatsapp':\n        return 1\n    return 0\n"
    assert {name for name, _ in _channel_names_in_conditionals(branching)} == {
        "whatsapp"
    }

    # And the negative half: a channel name in a docstring or an unconditional
    # assignment is NOT a branch. Without this the detector would fire on the
    # module's own documentation, and the only way to keep it green would be to
    # stop explaining the design.
    documented = '"""Sub declares whatsapp and email."""\nDEFAULT = "email"\n'
    assert _channel_names_in_conditionals(documented) == set()


def test_the_package_scan_is_not_empty() -> None:
    """Second half of the sensitivity proof: the scan sees real files."""
    scanned = {path.name for path in _package_sources()}
    assert {"lifecycle.py", "threading.py", "models.py"} <= scanned


# ── The fixed and closed vocabularies ────────────────────────────────────────


def test_the_channel_vocabulary_is_the_kernel_s_and_not_this_module_s() -> None:
    """The registry moved to `dotmac_kernel.channels` (2026-08-12).

    Consent, channel policy and delivery all need the same channel facts, and a
    module none of them may import cannot be their source — so a second registry
    here would have been the very collision this module's audit identified. What
    is left is a CONSUMER: `threading` reads two of the four traits and owns
    none. The trait vocabulary itself is pinned in
    `tests/unit/test_kernel_channels.py`.
    """
    assert not (MODULE_ROOT / "channels.py").exists()
    source = Path(inspect.getfile(threading)).read_text(encoding="utf-8")
    assert "from dotmac_kernel.channels import" in source


def test_the_status_vocabulary_is_exactly_four_terms() -> None:
    """Closed. A product that needs a fifth answer has found a REASON.

    CRM's `resolved_to_ticket` is the worked example: modelled as a status, it
    forces every membership set in every consumer to know about ticket handoff
    before it can answer "is this conversation live".
    """
    assert [status.value for status in lifecycle.Status] == [
        "open",
        "pending",
        "snoozed",
        "resolved",
    ]


def test_direction_is_exactly_three_terms() -> None:
    """Identical in both source products; there is no third opinion available."""
    assert [d.value for d in lifecycle.Direction] == ["inbound", "outbound", "internal"]


# ── D1 database identity ─────────────────────────────────────────────────────


def test_the_manifest_matches_its_ledger_row() -> None:
    """A stateful module whose declaration drifts from the ledger cannot boot.

    `NamespaceRegistry.from_manifests` refuses it, so this test only turns a
    boot failure into a build failure.
    """
    row = next(o for o in MIGRATION_OWNER_LEDGER if o.owner == "inbox")
    assert module.short_code == "ibx"
    assert module.migration_prefix == row.prefix == "ib"
    assert module.migration_branch == row.branch_label == "inbox"
    assert row.db_schema == module_schema("ibx") == models.SCHEMA == "mod_ibx"


def test_the_manifest_declares_exactly_the_tables_the_module_creates() -> None:
    """Both directions. A module claiming a table it does not create makes the
    live-catalog gate wrong in the direction that matters."""
    assert set(module.tables) == {"inbox_conversations", "inbox_messages"}


def test_every_model_is_bound_to_the_module_schema() -> None:
    """Fully qualified, never resolved through `search_path` — connection state
    a pooler or another module can change."""
    for model in (models.InboxConversation, models.InboxMessage):
        assert model.__table__.schema == "mod_ibx", model.__name__


def test_the_lineage_is_a_single_rooted_revision() -> None:
    revisions = sorted(MIGRATIONS.glob("ib_*.py"))
    assert [p.name for p in revisions] == ["ib_0001_conversations.py"]
    source = revisions[0].read_text(encoding="utf-8")
    assert 'revision = "ib_0001_conversations"' in source
    assert "down_revision = None" in source
    assert 'branch_labels = ("inbox",)' in source
    # Cross-lineage ordering is `depends_on`, never `down_revision` — the latter
    # would splice two independently released lineages into one chain.
    assert 'depends_on = ("0001_initial_tenant_schema",)' in source


# ── Hard rule 11: tenancy in the same migration ──────────────────────────────


@pytest.mark.parametrize("table", ["inbox_conversations", "inbox_messages"])
def test_every_table_is_tenant_scoped_with_forced_rls(table: str) -> None:
    """`tenant_id NOT NULL` + RLS ENABLEd *and* FORCEd + a policy + grants.

    FORCE matters: without it the table owner, which migrations run as, bypasses
    its own policy.
    """
    raw = (MIGRATIONS / "ib_0001_conversations.py").read_text(encoding="utf-8")
    # Collapse Python's string-literal wrapping before matching: a GRANT split
    # across two source lines to satisfy the line-length limit is the same SQL,
    # and asserting on the unwrapped form would make formatting a test failure.
    source = " ".join(raw.replace('"\n        "', "").split())

    assert f"ALTER TABLE mod_ibx.{table} ENABLE ROW LEVEL SECURITY;" in source
    assert f"ALTER TABLE mod_ibx.{table} FORCE ROW LEVEL SECURITY;" in source
    assert f"{table}_tenant_isolation" in source
    for role in ("app_user", "platform_api"):
        assert (
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON mod_ibx.{table} TO {role};"
            in source
        ), f"{table} is missing its {role} grant"

    model = {
        "inbox_conversations": models.InboxConversation,
        "inbox_messages": models.InboxMessage,
    }[table]
    assert model.__table__.c.tenant_id.nullable is False


def test_child_tables_reference_their_parent_by_composite_key() -> None:
    """A bare `conversation_id` would let one tenant's message attach to another
    tenant's conversation the moment an id leaked."""
    for model in (models.InboxMessage,):
        composite = [
            fk
            for fk in model.__table__.foreign_key_constraints
            if len(fk.column_keys) == 2 and "tenant_id" in fk.column_keys
        ]
        assert composite, (
            f"{model.__name__} must reference its parent through "
            "(tenant_id, <id>), not a bare id"
        )


# ── The narrowing: what must NOT appear ──────────────────────────────────────


def test_no_model_carries_a_product_domain_subject_column() -> None:
    """No `subscriber_id`, no `person_id`, no `ticket_id`.

    Sub's conversation FKs to `subscribers` and CRM's to `people` NOT NULL. A
    module that FK'd to either would force every consumer to adopt that
    product's identity model before it could adopt a conversation record — which
    is exactly what ERP, the strongest candidate consumer, could not do.
    """
    forbidden = {
        "subscriber_id",
        "person_id",
        "reseller_id",
        "ticket_id",
        "lead_id",
        "customer_id",
        "organization_id",
        "service_team_id",
    }
    for model in (models.InboxConversation, models.InboxMessage):
        leaked = forbidden & set(model.__table__.c.keys())
        assert not leaked, (
            f"{model.__name__} carries product-domain column(s) {sorted(leaked)}; "
            "products link from their own schema"
        )


def test_no_model_carries_outbound_delivery_state() -> None:
    """The kernel outbox owns delivery. A second `attempts`/`next_attempt_at`
    triple here — CRM has exactly that in `crm_outbox` — would be a second
    writer of one concern."""
    forbidden = {"attempts", "next_attempt_at", "last_attempt_at", "delivery_status"}
    for model in (models.InboxConversation, models.InboxMessage):
        leaked = forbidden & set(model.__table__.c.keys())
        assert not leaked, f"{model.__name__} carries delivery state {sorted(leaked)}"


def test_the_threading_rules_take_no_database_session() -> None:
    """`thread_key`/`dedup_key` are pure functions over a declaration and a
    payload. That is what lets every trait combination be tested exhaustively —
    and what stops the dedup rule drifting into three call sites the way it did
    in both source products."""
    for fn in (threading.thread_key, threading.dedup_key):
        params = set(inspect.signature(fn).parameters)
        assert params == {"identity"}, fn.__name__
    source = Path(inspect.getfile(threading)).read_text(encoding="utf-8")
    assert "Session" not in source
    assert "sqlalchemy" not in source


def test_the_package_declares_no_capability_without_a_route_to_gate() -> None:
    """A declared code with no consumer is dead vocabulary that reads as a
    working gate — the failure ADR-0008's registries exist to prevent. This
    release ships no routers, so it declares none."""
    assert module.capabilities == ()
    assert module.permissions == ()
    assert module.audit_actions == ()
