"""Source-application attribution, and a rotation window that does not drop calls.

Two things a machine credential could not previously express, added to the table
`0027_machine_credential` created and to the audit trail that records what those
credentials do.

## 1. WHICH APPLICATION — `machine_credentials.source_application`

Neither source product records it. Sub identifies its CRM caller by the presence
of an `integration:crm` scope (`app/api/crm.py::require_crm_service_auth`), which
makes identity a side effect of authorization: grant that scope to a second key
and the two callers become permanently indistinguishable in the trail. So
attribution gets its own column and its own question.

**Nullable, and only for this release.** There is no correct backfill: a stored
digest holds neither the raw key nor anything identifying its holder, and an
audit or credential column filled by guessing is worse than one left open —
a guess is indistinguishable from a fact once written. So the expand step lands
the column open, an operator attributes each row, and the NOT NULL is a separate
contract migration.

Open at rest is NOT open at runtime: `machine_auth.authenticate_machine` refuses
a credential whose `source_application` is NULL, with the same opaque message
every other refusal uses. An un-attributed credential therefore stops working the
moment this migration lands, which is the intended blast radius — the same
"unresolved active key is a CUTOVER BLOCKER" rule the ERP adoption plan states in
`docs/inventories/machine-credential-sources.md`.

`audit_events.source_application` is nullable for the different, permanent
reason `actor_type` is: rows written before this revision never recorded one and
are not rewritten. Going forward `write_audit_event` refuses to write without a
resolvable attribution, so the NULLs are bounded by this deployment's history
rather than by anybody's diligence.

## 2. A ROTATION WINDOW — `next_key_hash`, `rotation_started_at`, `rotated_at`

Sub's `rotate_api_key` overwrites `key_hash` in place; its own docstring says
"the old secret stops working immediately". Every caller still holding the
previous key fails from that instant until somebody redeploys it — an outage for
exactly the unattended callers this table serves.

Two digests, one row. Both authenticate, to the same principal with the same
scopes and the same attribution, until an explicit `complete_rotation` retires
the outgoing one. Nothing in the schema or the code retires it on a timer:
`rotation_started_at` exists so an ageing window is a QUERY, and it is
deliberately not an input to any authentication decision — a window that closed
itself while a caller still held the old key would reproduce the very failure it
was added to prevent.

The constraints are the parts that cannot be argued with later:

* `uq_..._tenant_next_key_hash` — composite with `tenant_id`, exactly like the
  existing `key_hash` unique and for the same isolation and non-disclosure
  reasons recorded in `0027`. NULLs do not collide, so a credential that is not
  rotating sits outside it entirely.
* `ck_..._next_key_hash_scheme` — the incoming digest is HMAC, same as the
  outgoing. A weaker scheme must not be able to enter through the new column.
* `ck_..._next_key_hash_differs` — rotating to the secret already held is not a
  rotation; permitting it makes `complete_rotation` a no-op that reports success.
* `ck_..._rotation_pair` — hash and start time move together, or the window is
  unreadable in both directions: a next hash with no start is a second live
  secret nobody can date, and a start with no hash is a rotation somebody
  believes is open.

Revision ID: 0028_machine_attribution
Revises: 0027_machine_credential
Create Date: 2026-08-24
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0028_machine_attribution"
down_revision = "0027_machine_credential"
branch_labels = None
depends_on = None

_TABLE = "machine_credentials"
_AUDIT = "audit_events"

#: Kept identical to `dotmac_kernel.source_applications
#: .SOURCE_APPLICATION_MAX_LENGTH` and to both ORM columns. A code that passed
#: validation and then truncated in storage would be a WRONG attribution rather
#: than a missing one, which is the failure mode this width exists to avoid.
_CODE_WIDTH = 64


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column("source_application", sa.String(_CODE_WIDTH), nullable=True),
    )
    op.add_column(_TABLE, sa.Column("next_key_hash", sa.String(120), nullable=True))
    op.add_column(
        _TABLE,
        sa.Column("rotation_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        _TABLE, sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True)
    )

    op.create_index(f"ix_{_TABLE}_source_application", _TABLE, ["source_application"])
    op.create_unique_constraint(
        f"uq_{_TABLE}_tenant_next_key_hash", _TABLE, ["tenant_id", "next_key_hash"]
    )
    op.create_check_constraint(
        f"ck_{_TABLE}_next_key_hash_scheme",
        _TABLE,
        "next_key_hash IS NULL OR next_key_hash LIKE 'hmac-sha256:%'",
    )
    op.create_check_constraint(
        f"ck_{_TABLE}_next_key_hash_differs",
        _TABLE,
        "next_key_hash IS NULL OR next_key_hash <> key_hash",
    )
    op.create_check_constraint(
        f"ck_{_TABLE}_rotation_pair",
        _TABLE,
        "(next_key_hash IS NULL) = (rotation_started_at IS NULL)",
    )
    op.create_check_constraint(
        f"ck_{_TABLE}_source_application_shape",
        _TABLE,
        "source_application IS NULL OR ("
        "length(source_application) > 1 "
        "AND trim(source_application) = source_application)",
    )

    # The audit trail's half. No RLS work here: `audit_events` already carries
    # its policy and grants from the revision that created it, and a column
    # addition inherits both.
    op.add_column(
        _AUDIT,
        sa.Column("source_application", sa.String(_CODE_WIDTH), nullable=True),
    )
    op.create_index(f"ix_{_AUDIT}_source_application", _AUDIT, ["source_application"])


def downgrade() -> None:
    op.drop_index(f"ix_{_AUDIT}_source_application", table_name=_AUDIT)
    op.drop_column(_AUDIT, "source_application")

    op.drop_constraint(f"ck_{_TABLE}_source_application_shape", _TABLE, type_="check")
    op.drop_constraint(f"ck_{_TABLE}_rotation_pair", _TABLE, type_="check")
    op.drop_constraint(f"ck_{_TABLE}_next_key_hash_differs", _TABLE, type_="check")
    op.drop_constraint(f"ck_{_TABLE}_next_key_hash_scheme", _TABLE, type_="check")
    op.drop_constraint(f"uq_{_TABLE}_tenant_next_key_hash", _TABLE, type_="unique")
    op.drop_index(f"ix_{_TABLE}_source_application", table_name=_TABLE)
    op.drop_column(_TABLE, "rotated_at")
    op.drop_column(_TABLE, "rotation_started_at")
    op.drop_column(_TABLE, "next_key_hash")
    op.drop_column(_TABLE, "source_application")
