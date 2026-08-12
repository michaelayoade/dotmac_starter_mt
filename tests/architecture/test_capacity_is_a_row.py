"""ADR-0019 § 5a: a commercial capacity is a row, never a column.

One Lagos company can be a subscriber, a reseller and a vendor at the same
moment — buying connectivity, reselling it, and supplying fibre. A single-valued
type column on an identity table cannot express that, and every such column in
the fleet is the same defect: Sub's and CRM's `account_type`, CRM's
`party_status` ladder, ERP's `customer_type`.

None of those live in this repository. This test stops the fifth one being added
here, which is the only part of the rule this repo can actually enforce.

**Detection is by VALUE, not by column name.** A name blacklist
(`account_type`, `party_status`, …) only catches the four names already known to
be wrong; the next one will be called something else. What makes a column a
capacity is that its permitted values name commercial capacities, so that is
what is checked — through a SQLAlchemy `Enum`'s members and through CHECK
constraint text.

`Party.party_type` (person | organization) is deliberately NOT caught: it is the
archetype's subtype discriminator — what kind of thing this is — not what
capacity it holds toward us. That distinction is the whole reason the detector
matches on capacity vocabulary rather than on "looks like a type column".
"""

from __future__ import annotations

import sqlalchemy as sa
from dotmac_kernel.models import Base

#: Commercial capacities: what a party is *to us*. Kept deliberately short and
#: unambiguous — a term goes here only if it can never be a legal form, a
#: lifecycle state, or a document kind. `individual`/`company` are absent for
#: exactly that reason (they are ERP's legal-form values), and so is `active`.
CAPACITY_TERMS = frozenset(
    {
        "agent",
        "customer",
        "distributor",
        "lead",
        "partner",
        "prospect",
        "reseller",
        "subscriber",
        "supplier",
        "vendor",
    }
)

#: Identity tables. Capacity belongs in rows of a party-role table, never on
#: these. A future kernel `party_roles` (the archetype's capacity table) is NOT
#: listed: it is where these values are *supposed* to live.
IDENTITY_TABLES = ("parties", "party_persons", "party_organizations")


def _capacity_hits(values: object) -> set[str]:
    """Capacity terms among a column's permitted values, matched whole-word.

    Substring matching would flag `vendor_invoice_id`; equality on the lowered
    value is what distinguishes an enumerated capacity from a name containing
    one.
    """
    if not isinstance(values, list | tuple | set | frozenset):
        return set()
    return {str(v).strip().lower() for v in values} & CAPACITY_TERMS


def _offending_columns(table: sa.Table) -> list[str]:
    offenders = []
    for column in table.columns:
        enums = getattr(column.type, "enums", None)
        if _capacity_hits(enums):
            offenders.append(f"{table.name}.{column.name} (Enum)")
    return offenders


def _offending_checks(table: sa.Table) -> list[str]:
    """A String column plus a CHECK is the other way to spell a closed enum.

    Sub writes its capacity constraints this way (`ck_party_roles_type`), so a
    detector that only understood SQLAlchemy `Enum` would miss the shape most
    likely to arrive here by copy.
    """
    offenders = []
    for constraint in table.constraints:
        if not isinstance(constraint, sa.CheckConstraint):
            continue
        text = str(constraint.sqltext).lower()
        quoted = {term for term in CAPACITY_TERMS if f"'{term}'" in text}
        if quoted:
            offenders.append(
                f"{table.name} CHECK {constraint.name or '<unnamed>'} "
                f"enumerates {sorted(quoted)}"
            )
    return offenders


def _scan(table: sa.Table) -> list[str]:
    return _offending_columns(table) + _offending_checks(table)


def test_no_identity_table_enumerates_a_commercial_capacity() -> None:
    offenders: list[str] = []
    for name in IDENTITY_TABLES:
        table = Base.metadata.tables.get(name)
        if table is None:
            continue
        offenders.extend(_scan(table))

    assert not offenders, (
        "ADR-0019 § 5a — a commercial capacity is a row, never a column:\n  "
        + "\n  ".join(offenders)
        + "\n\nOne party can hold several capacities at once (subscriber + "
        "reseller + vendor is ordinary for an ISP). A single-valued column "
        "cannot express that. Model it as rows in a party-role table, each "
        "independently suspendable and with its own validity window."
    )


def test_party_type_is_not_treated_as_a_capacity() -> None:
    """The discriminator must survive: a detector that flags it is too broad.

    `party_type` (person | organization) is the archetype's subtype
    discriminator. If a future edit widened CAPACITY_TERMS enough to catch it,
    the rule above would forbid the very column the archetype requires.
    """
    parties = Base.metadata.tables.get("parties")
    assert parties is not None, "the kernel must declare a `parties` table"

    party_type = parties.columns["party_type"]
    assert not _capacity_hits(getattr(party_type.type, "enums", None)), (
        "`parties.party_type` is being read as a commercial capacity. It is the "
        "person/organization subtype discriminator — CAPACITY_TERMS has grown "
        "too broad."
    )


def test_the_detector_actually_detects() -> None:
    """Sensitivity proof (ADR-0018): a detector must fail on a known offender.

    Without this, deleting CAPACITY_TERMS or breaking `_capacity_hits` would
    make every assertion above pass vacuously — the guard would report
    compliance precisely because it had stopped looking.
    """
    scratch = sa.MetaData()

    enum_offender = sa.Table(
        "synthetic_parties_enum",
        scratch,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "account_type",
            sa.Enum("customer", "reseller", "vendor", name="synthetic_account_type"),
        ),
    )
    assert _scan(
        enum_offender
    ), "the Enum detector missed a column enumerating customer/reseller/vendor"

    check_offender = sa.Table(
        "synthetic_parties_check",
        scratch,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("capacity", sa.String(32)),
        sa.CheckConstraint(
            "capacity IN ('subscriber', 'reseller')", name="ck_synthetic_capacity"
        ),
    )
    assert _scan(
        check_offender
    ), "the CHECK detector missed a constraint enumerating subscriber/reseller"

    clean = sa.Table(
        "synthetic_parties_clean",
        scratch,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("party_type", sa.Enum("person", "organization", name="synthetic_pt")),
        # Named for a capacity but not enumerating one: the case a
        # name-matching detector would wrongly flag.
        sa.Column("vendor_reference", sa.String(80)),
    )
    assert not _scan(clean), (
        "the detector flagged a clean table — `party_type` or a column merely "
        "named after a capacity is being matched"
    )
