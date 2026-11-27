"""Opening late items: post-seal source debits and credits (R8; tenant plane only).

After the opening is sealed, Finance can find a source open invoice the opening
did not carry (a debit) or a source credit note that was never applied (a credit
against one open invoice). This revision adds the append-only ``opening_late_items``
table: one typed row per recorded debit, credit or exact reversal, with a signed
``receivable_delta`` (debit positive, credit negative, reversal the exact
negation). No revenue and no tax is booked: the debit document is an issued
invoice with ``authoring_mode = 'opening_late_item'`` and no issuance posting.

The database, not only Python, bounds the online role (which holds INSERT):

* the admission trigger needs the sealed window, the sealed capture namespace,
  ``dual_control = 'off'``, and locks the account and then the document;
* one rule bounds every row: the room after the insert lies in ``[0, base]``,
  which gives both "a credit stays within the room" and "a debit is reversible
  only while untouched". A credit also stays within the room not claimed by a
  frozen expected-withholding component;
* a debit key carried by the opening, or by an unreversed debit, is refused;
  the credits of one source credit note stay on one account and within its total;
* a deferred check requires, for every R8 posting group, exactly one item, one
  V7 accounting fact and one matching receivable effect.

Three bi_0008 functions are rewritten by replacing exactly one occurrence of a
fixed fragment in their stored definition (the bi_0019 precedent), so everything
else in each body is byte-identical to bi_0008:

* ``opening_invoice_room_at``: base and issuance for an opening late document, and
  the non-debit item rows as contributions;
* ``has_live_invoice_issuance``: an opening late document is live while its debit is
  unreversed;
* ``admit_opening_exposure``: a group recorded by an item affects its document.

Two additive triggers refuse a generic void of an opening late document (or of a
document carrying a net item credit) and a generic reversal of an R8 group.

Tenant plane only; nothing is backfilled. No downgrade.
"""

from __future__ import annotations

# All DDL identifiers are closed Billing literals.
# ruff: noqa: S608
import sqlalchemy as sa
from dotmac_kernel.planes import ModulePlane, selected_module_planes

from alembic import op

revision = "bi_0021_opening_late_items"
down_revision = "bi_0020_withholding_releases"
branch_labels = None
depends_on = None
_SCHEMA = "mod_billing"
_TABLE = "opening_late_items"
_KINDS = "('opening_late_debit_recorded','opening_late_credit_applied','opening_late_item_reversed')"

# (signature, exactly-one old fragment, replacement). Applied in order.
_ROOM = "mod_billing.opening_invoice_room_at(uuid, uuid, bigint, uuid)"
_LIVE = "mod_billing.has_live_invoice_issuance(uuid, uuid, text, integer, uuid)"
_EXPOSURE = "mod_billing.admit_opening_exposure()"

_ROOM_BASE_OLD = "opening_base := backing.initial_receivable;"
_ROOM_BASE_NEW = """opening_base := backing.initial_receivable;
      ELSIF invoice.authoring_mode = 'opening_late_item' THEN
        SELECT x.receivable_delta, x.billing_account_id, x.currency, x.minor_units, g.source_version INTO backing
          FROM mod_billing.opening_late_items x
          JOIN mod_billing.posting_groups g ON g.id = x.posting_group_id AND g.tenant_id = x.tenant_id
          WHERE x.document_id = invoice_id AND x.kind = 'debit' AND x.tenant_id = tenant;
        IF backing.receivable_delta IS NULL OR backing.billing_account_id <> account_id OR backing.currency <> invoice.currency
          OR backing.minor_units <> invoice.minor_units OR backing.source_version > target_version THEN
          RAISE EXCEPTION 'opening late invoice requires its exact source-version debit' USING ERRCODE = 'check_violation';
        END IF;
        opening_base := backing.receivable_delta;"""
_ROOM_ISSUE_OLD = (
    "IF doc.id = invoice_id AND invoice.authoring_mode = 'imported_open_item' THEN"
)
_ROOM_ISSUE_NEW = """IF doc.id = invoice_id AND invoice.authoring_mode = 'opening_late_item' THEN
          IF EXISTS (SELECT 1 FROM mod_billing.document_events x WHERE x.document_id = invoice_id AND x.event_kind = 'voided' AND x.tenant_id = tenant) THEN
            RAISE EXCEPTION 'opening late invoice cannot be generically voided' USING ERRCODE = 'check_violation';
          END IF;
          CONTINUE;
        END IF;
        IF doc.id = invoice_id AND invoice.authoring_mode = 'imported_open_item' THEN"""
_ROOM_UNION_OLD = "FROM mod_billing.opening_cash_effects x WHERE x.document_id = invoice_id AND x.tenant_id = tenant"
_ROOM_UNION_NEW = """FROM mod_billing.opening_cash_effects x WHERE x.document_id = invoice_id AND x.tenant_id = tenant
        UNION ALL SELECT x.posting_group_id, x.billing_account_id, x.currency, x.minor_units, x.receivable_delta
          FROM mod_billing.opening_late_items x WHERE x.document_id = invoice_id AND x.kind <> 'debit' AND x.tenant_id = tenant"""
_LIVE_OLD = "SELECT CASE WHEN d.authoring_mode = 'imported_open_item' THEN"
_LIVE_NEW = """SELECT CASE WHEN d.authoring_mode = 'opening_late_item' THEN
        EXISTS (SELECT 1 FROM mod_billing.opening_late_items x
          JOIN mod_billing.posting_groups g ON g.id = x.posting_group_id AND g.tenant_id = x.tenant_id
          WHERE x.document_id = invoice_id AND x.billing_account_id = account_id AND x.currency = currency_code AND x.minor_units = scale AND x.tenant_id = tenant
            AND x.kind = 'debit' AND g.group_kind = 'opening_late_debit_recorded'
            AND NOT EXISTS (SELECT 1 FROM mod_billing.opening_late_items r WHERE r.reverses_item_id = x.id AND r.tenant_id = x.tenant_id))
        WHEN d.authoring_mode = 'imported_open_item' THEN"""
_EXPOSURE_OLD = "OR EXISTS (SELECT 1 FROM mod_billing.opening_import_receipts x WHERE x.posting_group_id = grp.id AND x.original_receipt_id = basis.receipt_id AND x.tenant_id = NEW.tenant_id)"
_EXPOSURE_ADD = """
        OR EXISTS (SELECT 1 FROM mod_billing.opening_late_items x WHERE x.posting_group_id = grp.id AND x.document_id = NEW.document_id AND x.tenant_id = NEW.tenant_id)"""  # nosec B608 -- fixed fragment, no input
_EXPOSURE_NEW = _EXPOSURE_OLD + _EXPOSURE_ADD

HUNKS = (
    (_ROOM, _ROOM_BASE_OLD, _ROOM_BASE_NEW),
    (_ROOM, _ROOM_ISSUE_OLD, _ROOM_ISSUE_NEW),
    (_ROOM, _ROOM_UNION_OLD, _ROOM_UNION_NEW),
    (_LIVE, _LIVE_OLD, _LIVE_NEW),
    (_EXPOSURE, _EXPOSURE_OLD, _EXPOSURE_NEW),
)


def _rewrite_rows() -> str:
    return ",\n        ".join(
        f"($sig${sig}$sig$, $o${old}$o$, $n${new}$n$)" for sig, old, new in HUNKS
    )


def upgrade() -> None:
    # Tenant-plane table: nothing to do for a platform-only composition.
    if ModulePlane.TENANT not in selected_module_planes("billing"):
        return
    op.execute(
        "SET LOCAL lock_timeout = '5s'; "
        "LOCK TABLE mod_billing.documents, mod_billing.document_events, "
        "mod_billing.posting_groups IN SHARE ROW EXCLUSIVE MODE;"
    )
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("window_id", sa.Uuid(), nullable=False),
        sa.Column("billing_account_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("source_installation_id", sa.String(255), nullable=False),
        sa.Column("source_system", sa.String(120), nullable=False),
        sa.Column("source_key", sa.String(255), nullable=False),
        sa.Column("source_number", sa.String(255)),
        sa.Column("generation", sa.Integer()),
        sa.Column("source_total", sa.Numeric(20, 6)),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("posting_group_id", sa.Uuid(), nullable=False),
        sa.Column("receivable_delta", sa.Numeric(20, 6), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("minor_units", sa.Integer(), nullable=False),
        sa.Column("reverses_item_id", sa.Uuid()),
        sa.Column("collection", sa.JSON()),
        sa.Column("collection_digest", sa.String(64)),
        sa.Column("evidence_ref", sa.String(255)),
        sa.Column("evidence_digest", sa.String(64)),
        sa.Column("reason", sa.Text()),
        sa.Column("actor_ref", sa.String(255), nullable=False),
        sa.Column("command_id", sa.String(255), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.clock_timestamp(),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            name=f"fk_{_TABLE}_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "window_id"],
            ["mod_billing.opening_windows.tenant_id", "mod_billing.opening_windows.id"],
            name=f"fk_{_TABLE}_window",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_id", "billing_account_id"],
            [
                "mod_billing.documents.tenant_id",
                "mod_billing.documents.id",
                "mod_billing.documents.billing_account_id",
            ],
            name=f"fk_{_TABLE}_document",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "posting_group_id", "billing_account_id"],
            [
                "mod_billing.posting_groups.tenant_id",
                "mod_billing.posting_groups.id",
                "mod_billing.posting_groups.billing_account_id",
            ],
            name=f"fk_{_TABLE}_group",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "id", name=f"uq_{_TABLE}_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "reverses_item_id"],
            [
                "mod_billing.opening_late_items.tenant_id",
                "mod_billing.opening_late_items.id",
            ],
            name=f"fk_{_TABLE}_reverses",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "command_id", name=f"uq_{_TABLE}_command"),
        sa.UniqueConstraint(
            "tenant_id", "reverses_item_id", name=f"uq_{_TABLE}_reversal"
        ),
        sa.UniqueConstraint("tenant_id", "posting_group_id", name=f"uq_{_TABLE}_group"),
        sa.CheckConstraint(
            "kind IN ('debit', 'credit', 'reversal') AND receivable_delta <> 0 "
            "AND ((kind = 'debit' AND receivable_delta > 0 "
            "AND reverses_item_id IS NULL AND generation >= 1 "
            "AND source_total IS NULL AND source_number IS NULL "
            "AND collection IS NOT NULL AND collection_digest IS NOT NULL "
            "AND evidence_ref IS NOT NULL "
            "AND evidence_digest IS NOT NULL AND reason IS NULL) "
            "OR (kind = 'credit' AND receivable_delta < 0 "
            "AND reverses_item_id IS NULL AND generation IS NULL "
            "AND source_total > 0 AND -receivable_delta <= source_total "
            "AND source_number IS NOT NULL AND collection IS NULL "
            "AND collection_digest IS NULL "
            "AND evidence_ref IS NOT NULL AND evidence_digest IS NOT NULL "
            "AND reason IS NULL) "
            "OR (kind = 'reversal' AND reverses_item_id IS NOT NULL "
            "AND collection IS NULL AND collection_digest IS NULL "
            "AND evidence_ref IS NULL "
            "AND evidence_digest IS NULL AND reason IS NOT NULL))",
            name=f"ck_{_TABLE}_shape",
        ),
        sa.CheckConstraint(
            "currency = upper(currency) AND length(currency) = 3 "
            "AND minor_units BETWEEN 0 AND 6",
            name=f"ck_{_TABLE}_currency",
        ),
        sa.CheckConstraint(
            "length(trim(command_id)) > 0 AND length(trim(actor_ref)) > 0 "
            "AND length(trim(source_installation_id)) > 0 "
            "AND length(trim(source_system)) > 0 AND length(trim(source_key)) > 0 "
            "AND (source_number IS NULL OR length(trim(source_number)) > 0) "
            "AND (evidence_ref IS NULL OR length(trim(evidence_ref)) > 0) "
            "AND (evidence_digest IS NULL OR length(evidence_digest) = 64) "
            "AND (collection_digest IS NULL OR length(collection_digest) = 64) "
            "AND (reason IS NULL OR length(trim(reason)) > 0)",
            name=f"ck_{_TABLE}_attribution",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        f"uq_{_TABLE}_debit_claim",
        _TABLE,
        [
            "tenant_id",
            "source_installation_id",
            "source_system",
            "source_key",
            "generation",
        ],
        unique=True,
        schema=_SCHEMA,
        postgresql_where=sa.text("kind = 'debit'"),
    )
    op.create_index(
        f"ix_{_TABLE}_document",
        _TABLE,
        ["tenant_id", "document_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        f"uq_{_TABLE}_debit_document",
        _TABLE,
        ["tenant_id", "document_id"],
        unique=True,
        schema=_SCHEMA,
        postgresql_where=sa.text("kind = 'debit'"),
    )
    op.execute(f"""
        ALTER TABLE mod_billing.{_TABLE} ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_billing.{_TABLE} FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON mod_billing.{_TABLE}
          USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
          WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);
        REVOKE ALL ON mod_billing.{_TABLE} FROM PUBLIC, app_user, platform_api;
        GRANT SELECT, INSERT ON mod_billing.{_TABLE} TO app_user;
        CREATE TRIGGER {_TABLE}_append_only BEFORE UPDATE OR DELETE
          ON mod_billing.{_TABLE}
          FOR EACH ROW EXECUTE FUNCTION mod_billing.refuse_mutation();
    """)  # nosec B608 -- closed table identifiers; no input reaches this DDL
    op.execute(f"""
        CREATE FUNCTION mod_billing.admit_opening_late_item() RETURNS trigger AS $$
        DECLARE
            win record; doc record; grp record; orig record;
            capture jsonb; room numeric; base numeric; undischarged numeric;
            n bigint; open_n bigint; applied numeric; other_n bigint;
        BEGIN
            -- Database time, never the caller's.
            NEW.recorded_at := clock_timestamp();
            -- House lock order: the account first, then the document.
            PERFORM 1 FROM mod_billing.billing_accounts a
             WHERE a.id = NEW.billing_account_id AND a.tenant_id = NEW.tenant_id
             FOR UPDATE;
            SELECT * INTO win FROM mod_billing.opening_windows w
             WHERE w.id = NEW.window_id AND w.tenant_id = NEW.tenant_id;
            IF win.id IS NULL OR NOT EXISTS (
                SELECT 1 FROM mod_billing.opening_window_seals s
                 WHERE s.window_id = win.id AND s.tenant_id = NEW.tenant_id)
            THEN
                RAISE EXCEPTION 'an opening late item needs the sealed opening window'
                  USING ERRCODE = 'check_violation';
            END IF;
            IF coalesce(win.evidence::jsonb->'window'->>'dual_control', 'on') <> 'off' THEN
                RAISE EXCEPTION 'opening late items are unsupported while the window binds dual control on'
                  USING ERRCODE = 'check_violation';
            END IF;
            capture := win.evidence::jsonb->'window'->'manifest'->'capture';
            IF NEW.source_installation_id IS DISTINCT FROM capture->>'installation_id'
               OR NEW.source_system IS DISTINCT FROM capture->>'source_system' THEN
                RAISE EXCEPTION 'an opening late item must name the sealed capture namespace'
                  USING ERRCODE = 'check_violation';
            END IF;
            -- A held partition takes no item until its governed admission.
            IF coalesce((SELECT s.evidence::jsonb->'command'->'held_account_ids'
                           FROM mod_billing.opening_window_seals s
                          WHERE s.window_id = win.id AND s.tenant_id = NEW.tenant_id),
                        '[]'::jsonb) ? NEW.billing_account_id::text
               AND NOT EXISTS (
                    SELECT 1 FROM mod_billing.opening_hold_admissions h
                     WHERE h.billing_account_id = NEW.billing_account_id
                       AND h.tenant_id = NEW.tenant_id)
            THEN
                RAISE EXCEPTION 'a held opening partition takes no opening late item before its admission'
                  USING ERRCODE = 'check_violation';
            END IF;
            SELECT * INTO doc FROM mod_billing.documents x
             WHERE x.id = NEW.document_id AND x.tenant_id = NEW.tenant_id
             FOR UPDATE;
            SELECT * INTO grp FROM mod_billing.posting_groups g
             WHERE g.id = NEW.posting_group_id AND g.tenant_id = NEW.tenant_id;
            IF doc.id IS NULL OR grp.id IS NULL OR doc.document_kind <> 'invoice'
               OR doc.issued_at IS NULL
               OR doc.billing_account_id IS DISTINCT FROM NEW.billing_account_id
               OR grp.billing_account_id IS DISTINCT FROM NEW.billing_account_id
               OR doc.currency <> NEW.currency OR doc.minor_units <> NEW.minor_units
               OR grp.currency <> NEW.currency OR grp.minor_units <> NEW.minor_units
               OR NEW.receivable_delta <> round(NEW.receivable_delta, NEW.minor_units)
               OR grp.group_kind <> (CASE NEW.kind
                    WHEN 'debit' THEN 'opening_late_debit_recorded'
                    WHEN 'credit' THEN 'opening_late_credit_applied'
                    ELSE 'opening_late_item_reversed' END)
            THEN
                RAISE EXCEPTION 'an opening late item needs its issued invoice, group and exact money'
                  USING ERRCODE = 'check_violation';
            END IF;
            IF EXISTS (
                SELECT 1 FROM mod_billing.document_events e
                 WHERE e.document_id = NEW.document_id
                   AND e.tenant_id = NEW.tenant_id AND e.event_kind = 'voided'
            ) THEN
                RAISE EXCEPTION 'a voided invoice cannot take an opening late item'
                  USING ERRCODE = 'check_violation';
            END IF;

            IF NEW.kind = 'debit' THEN
                IF doc.authoring_mode <> 'opening_late_item' OR doc.obligation_id IS NOT NULL
                   OR doc.grand_total < NEW.receivable_delta THEN
                    RAISE EXCEPTION 'an opening late debit needs its opening late document'
                      USING ERRCODE = 'check_violation';
                END IF;
                IF EXISTS (
                    SELECT 1 FROM mod_billing.opening_receivable_bases b
                     WHERE b.tenant_id = NEW.tenant_id
                       AND b.installation_id = NEW.source_installation_id
                       AND b.source_system = NEW.source_system
                       AND b.source_invoice_key = NEW.source_key
                ) THEN
                    RAISE EXCEPTION 'the source invoice is carried by the opening'
                      USING ERRCODE = 'unique_violation';
                END IF;
                SELECT count(*) INTO n
                  FROM mod_billing.opening_late_items x
                 WHERE x.tenant_id = NEW.tenant_id AND x.kind = 'debit'
                   AND x.source_installation_id = NEW.source_installation_id
                   AND x.source_system = NEW.source_system
                   AND x.source_key = NEW.source_key;
                SELECT count(*) INTO open_n
                  FROM mod_billing.opening_late_items x
                 WHERE x.tenant_id = NEW.tenant_id AND x.kind = 'debit'
                   AND x.source_installation_id = NEW.source_installation_id
                   AND x.source_system = NEW.source_system
                   AND x.source_key = NEW.source_key
                   AND NOT EXISTS (
                       SELECT 1 FROM mod_billing.opening_late_items r
                        WHERE r.reverses_item_id = x.id AND r.tenant_id = x.tenant_id);
                IF open_n > 0 OR NEW.generation <> n + 1 THEN
                    RAISE EXCEPTION 'the source invoice already has an unreversed opening late debit'
                      USING ERRCODE = 'unique_violation';
                END IF;
                RETURN NEW;
            END IF;

            -- Credit or reversal: the room after the insert lies in [0, base].
            base := (CASE doc.authoring_mode
                WHEN 'opening_late_item' THEN (
                    SELECT d.receivable_delta FROM mod_billing.opening_late_items d
                     WHERE d.document_id = doc.id AND d.kind = 'debit'
                       AND d.tenant_id = NEW.tenant_id)
                WHEN 'imported_open_item' THEN (
                    SELECT b.initial_receivable FROM mod_billing.opening_receivable_bases b
                     WHERE b.document_id = doc.id AND b.tenant_id = NEW.tenant_id)
                ELSE doc.grand_total END);
            room := mod_billing.opening_invoice_room(NEW.document_id, NEW.tenant_id);
            IF base IS NULL OR room IS NULL
               OR room + NEW.receivable_delta < 0 OR room + NEW.receivable_delta > base THEN
                RAISE EXCEPTION 'an opening late item must keep the invoice room between zero and its base'
                  USING ERRCODE = 'check_violation';
            END IF;

            IF NEW.kind = 'credit' THEN
                IF NOT mod_billing.has_live_invoice_issuance(
                    NEW.document_id, NEW.billing_account_id, NEW.currency, NEW.minor_units, NEW.tenant_id)
                THEN
                    RAISE EXCEPTION 'an opening late credit needs a live invoice'
                      USING ERRCODE = 'check_violation';
                END IF;
                -- Frozen expected withholding not yet discharged or released
                -- is not room a credit may consume (components - discharges - releases).
                SELECT greatest(
                    coalesce((SELECT sum(c.tax_amount) FROM mod_billing.document_withholding_components c
                               WHERE c.document_id = NEW.document_id AND c.tenant_id = NEW.tenant_id), 0)
                    - coalesce((SELECT sum(w.amount_delta) FROM mod_billing.withholding_discharges w
                                 WHERE w.document_id = NEW.document_id AND w.tenant_id = NEW.tenant_id), 0)
                    - coalesce((SELECT sum(r.amount_delta) FROM mod_billing.withholding_releases r
                                 WHERE r.document_id = NEW.document_id AND r.tenant_id = NEW.tenant_id), 0),
                    0) INTO undischarged;
                IF -NEW.receivable_delta > room - undischarged THEN
                    RAISE EXCEPTION 'an opening late credit exceeds the non-withholding room'
                      USING ERRCODE = 'check_violation';
                END IF;
                -- One source credit note: one account, one total, never over it.
                PERFORM pg_advisory_xact_lock(hashtextextended(
                    NEW.tenant_id::text || '|' || NEW.source_installation_id || '|'
                    || NEW.source_system || '|credit|' || NEW.source_key, 0));
                SELECT count(*) FILTER (WHERE x.billing_account_id <> NEW.billing_account_id
                                           OR x.source_total IS DISTINCT FROM NEW.source_total
                                           OR x.source_number IS DISTINCT FROM NEW.source_number),
                       coalesce(sum(-x.receivable_delta), 0)
                  INTO other_n, applied
                  FROM mod_billing.opening_late_items x
                 WHERE x.tenant_id = NEW.tenant_id AND x.generation IS NULL
                   AND x.source_installation_id = NEW.source_installation_id
                   AND x.source_system = NEW.source_system
                   AND x.source_key = NEW.source_key;
                IF other_n > 0 OR applied - NEW.receivable_delta > NEW.source_total THEN
                    RAISE EXCEPTION 'an opening late credit stays on one account and within its credit note total'
                      USING ERRCODE = 'check_violation';
                END IF;
                RETURN NEW;
            END IF;

            -- Reversal: the exact negation of an unreversed debit or credit.
            SELECT * INTO orig FROM mod_billing.opening_late_items o
             WHERE o.id = NEW.reverses_item_id AND o.tenant_id = NEW.tenant_id;
            IF orig.id IS NULL OR orig.kind = 'reversal'
               OR NEW.receivable_delta <> -orig.receivable_delta
               OR NEW.billing_account_id <> orig.billing_account_id
               OR NEW.document_id <> orig.document_id OR NEW.window_id <> orig.window_id
               OR NEW.currency <> orig.currency OR NEW.minor_units <> orig.minor_units
               OR NEW.source_installation_id <> orig.source_installation_id
               OR NEW.source_system <> orig.source_system OR NEW.source_key <> orig.source_key
               OR NEW.source_number IS DISTINCT FROM orig.source_number
               OR NEW.generation IS DISTINCT FROM orig.generation
               OR NEW.source_total IS DISTINCT FROM orig.source_total
            THEN
                RAISE EXCEPTION 'an opening late reversal must be the exact negation of its item'
                  USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER {_TABLE}_admission BEFORE INSERT ON mod_billing.{_TABLE}
          FOR EACH ROW EXECUTE FUNCTION mod_billing.admit_opening_late_item();
    """)  # nosec B608 -- closed identifiers; no input reaches this DDL
    # Every R8 group completes at commit: one item, one V7 fact, one effect.
    op.execute(f"""
        CREATE FUNCTION mod_billing.check_opening_late_group_complete() RETURNS trigger AS $$
        DECLARE item record;
        BEGIN
            SELECT * INTO item FROM mod_billing.{_TABLE} x
             WHERE x.posting_group_id = NEW.id AND x.tenant_id = NEW.tenant_id;
            IF item.id IS NULL
               OR (SELECT count(*) FROM mod_billing.{_TABLE} x
                    WHERE x.posting_group_id = NEW.id AND x.tenant_id = NEW.tenant_id) <> 1
               OR (SELECT count(*) FROM mod_billing.accounting_facts f
                    WHERE f.posting_group_id = NEW.id AND f.tenant_id = NEW.tenant_id) <> 1
               OR NOT EXISTS (
                    SELECT 1 FROM mod_billing.accounting_facts f
                     WHERE f.posting_group_id = NEW.id AND f.tenant_id = NEW.tenant_id
                       AND f.fact_version = 7 AND f.effect_kind = NEW.group_kind
                       AND f.fact_payload::jsonb->'item'->>'item_id' = item.id::text
                       AND f.fact_payload::jsonb->'item'->>'kind' = item.kind
                       AND f.fact_payload::jsonb->'item'->>'document_id' = item.document_id::text)
               OR (SELECT count(*) FROM mod_billing.posting_effects e
                    WHERE e.posting_group_id = NEW.id AND e.tenant_id = NEW.tenant_id) <> 1
               OR NOT EXISTS (
                    SELECT 1 FROM mod_billing.posting_effects e
                     WHERE e.posting_group_id = NEW.id AND e.tenant_id = NEW.tenant_id
                       AND e.lane = 'receivable' AND e.amount_delta = item.receivable_delta
                       AND e.billing_account_id = item.billing_account_id
                       AND e.currency = item.currency AND e.minor_units = item.minor_units)
            THEN
                RAISE EXCEPTION 'an opening late group needs exactly one item, one V7 fact and its receivable effect'
                  USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE CONSTRAINT TRIGGER posting_groups_opening_late_complete
          AFTER INSERT ON mod_billing.posting_groups
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          WHEN (NEW.group_kind IN {_KINDS})
          EXECUTE FUNCTION mod_billing.check_opening_late_group_complete();

        -- An R8 group is reversed only by R8, and only R8 reverses it.
        CREATE FUNCTION mod_billing.guard_opening_late_group_reversal() RETURNS trigger AS $$
        DECLARE original text;
        BEGIN
            IF NEW.reverses_group_id IS NOT NULL THEN
                SELECT g.group_kind INTO original FROM mod_billing.posting_groups g
                 WHERE g.id = NEW.reverses_group_id AND g.tenant_id = NEW.tenant_id;
            END IF;
            IF (original IN ('opening_late_debit_recorded', 'opening_late_credit_applied', 'opening_late_item_reversed')
                AND NEW.group_kind <> 'opening_late_item_reversed')
               OR (NEW.group_kind = 'opening_late_item_reversed'
                   AND (original IS NULL
                        OR original NOT IN ('opening_late_debit_recorded', 'opening_late_credit_applied')))
            THEN
                RAISE EXCEPTION 'an opening late group is reversed only by its own owner'
                  USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER posting_groups_opening_late_reversal BEFORE INSERT
          ON mod_billing.posting_groups
          FOR EACH ROW EXECUTE FUNCTION mod_billing.guard_opening_late_group_reversal();

        -- An opening basis cannot take a source key with an unreversed R8 debit.
        CREATE FUNCTION mod_billing.guard_opening_late_basis_key() RETURNS trigger AS $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM mod_billing.{_TABLE} x
                 WHERE x.tenant_id = NEW.tenant_id AND x.kind = 'debit'
                   AND x.source_installation_id = NEW.installation_id
                   AND x.source_system = NEW.source_system
                   AND x.source_key = NEW.source_invoice_key
                   AND NOT EXISTS (
                       SELECT 1 FROM mod_billing.{_TABLE} r
                        WHERE r.reverses_item_id = x.id AND r.tenant_id = x.tenant_id))
            THEN
                RAISE EXCEPTION 'the source invoice has an unreversed opening late debit'
                  USING ERRCODE = 'unique_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER opening_receivable_bases_opening_late_key BEFORE INSERT
          ON mod_billing.opening_receivable_bases
          FOR EACH ROW EXECUTE FUNCTION mod_billing.guard_opening_late_basis_key();

        -- Additive: an opening late document, or one carrying a net item credit,
        -- cannot be generically voided (guard_imported_owner is not replaced).
        CREATE FUNCTION mod_billing.guard_opening_late_void() RETURNS trigger AS $$
        BEGIN
            IF NEW.event_kind = 'voided' AND (
                EXISTS (SELECT 1 FROM mod_billing.documents d
                         WHERE d.id = NEW.document_id AND d.tenant_id = NEW.tenant_id
                           AND d.authoring_mode = 'opening_late_item')
                OR (SELECT coalesce(sum(x.receivable_delta), 0) FROM mod_billing.{_TABLE} x
                     WHERE x.document_id = NEW.document_id AND x.tenant_id = NEW.tenant_id
                       AND x.kind <> 'debit') <> 0)
            THEN
                RAISE EXCEPTION 'an opening late document, or one with a net opening late credit, cannot be voided'
                  USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER document_events_opening_late_void BEFORE INSERT
          ON mod_billing.document_events
          FOR EACH ROW EXECUTE FUNCTION mod_billing.guard_opening_late_void();
    """)  # nosec B608 -- closed identifiers; no input reaches this DDL
    op.drop_constraint(
        "ck_documents_authoring_mode", "documents", schema=_SCHEMA, type_="check"
    )
    op.create_check_constraint(
        "ck_documents_authoring_mode",
        "documents",
        "authoring_mode IN ('legacy_single', 'explicit_multiline', "
        "'imported_open_item', 'opening_late_item')",
        schema=_SCHEMA,
    )
    rows = _rewrite_rows()
    op.execute(f"""
    -- mod_billing function rewrites: each fragment must occur exactly once.
    DO $rewrite$
    DECLARE spec record; def text; fixed text;
    BEGIN
      FOR spec IN SELECT * FROM (VALUES
        {rows}
      ) AS t(signature, old_text, new_text)
      LOOP
        def := pg_catalog.pg_get_functiondef(spec.signature::pg_catalog.regprocedure);
        IF (pg_catalog.length(def) - pg_catalog.length(pg_catalog.replace(def, spec.old_text, '')))
            <> pg_catalog.length(spec.old_text)
        THEN RAISE EXCEPTION 'bi_0021: expected exactly one occurrence in %', spec.signature; END IF;
        fixed := pg_catalog.replace(def, spec.old_text, spec.new_text);
        EXECUTE fixed;
      END LOOP;
    END $rewrite$;
    """)  # nosec B608 -- fixed fragments, no input


def downgrade() -> None:
    raise RuntimeError(
        "Opening late items are append-only financial evidence; no downgrade"
    )
