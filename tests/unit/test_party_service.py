"""Party-role, relationship, membership, and reachability parity canaries."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.models import (
    Party,
    PartyOrganization,
    PartyPerson,
    PartyType,
    Tenant,
)
from dotmac_party.contracts import (
    AddContactPoint,
    AssignPartyRole,
    ContactConsentStatus,
    ContactVerificationStatus,
    CreatePartyMembership,
    MembershipStatus,
    PartyConflict,
    PartyInvariantError,
    PartyNotFound,
    RecordExternalReference,
    RelatePartyRoles,
    RelationshipStatus,
    RoleStatus,
)
from dotmac_party.models import TENANT_TABLES
from dotmac_party.service import (
    add_contact_point,
    assign_role,
    create_membership,
    record_external_reference,
    relate_roles,
    set_contact_active,
    set_contact_consent,
    set_contact_verification,
    set_primary_contact,
    transition_membership,
    transition_relationship,
    transition_role,
)
from dotmac_party.vocabulary import (
    ContactChannelSpec,
    MembershipTypeSpec,
    PartyVocabularyRegistry,
    RelationshipTypeSpec,
    RoleTypeSpec,
    normalize_email,
    normalize_phone,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

TENANT_A = uuid.uuid4()
TENANT_B = uuid.uuid4()
PERSON_A = uuid.uuid4()
PERSON_B = uuid.uuid4()
ORGANIZATION_A = uuid.uuid4()


@pytest.fixture
def vocabulary() -> PartyVocabularyRegistry:
    return PartyVocabularyRegistry(
        role_types=(
            RoleTypeSpec("contact", "A known representative", "test"),
            RoleTypeSpec("customer", "A customer capacity", "test"),
            RoleTypeSpec(
                "partner", "A typed partner capacity", "test", key_required=True
            ),
        ),
        relationship_types=(
            RelationshipTypeSpec(
                "billing_contact_for",
                "Billing representative",
                "test",
                subject_role_types=frozenset({"contact"}),
                object_role_types=frozenset({"customer"}),
            ),
        ),
        membership_types=(
            MembershipTypeSpec(
                "admin",
                "Organization administrator",
                "test",
                access_scope_keys=frozenset({"accounts"}),
            ),
        ),
        contact_channels=(
            ContactChannelSpec("email", "Email address", "test", normalize_email),
            ContactChannelSpec("phone", "Telephone number", "test", normalize_phone),
            ContactChannelSpec(
                "social",
                "Provider-scoped social identity",
                "test",
                str.strip,
                requires_provider_identity=True,
            ),
        ),
    )


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_party": None}},
    )
    Tenant.__table__.create(engine)
    Party.__table__.create(engine)
    PartyPerson.__table__.create(engine)
    PartyOrganization.__table__.create(engine)
    from dotmac_party import models

    for table_name in TENANT_TABLES:
        models.metadata_table(table_name).create(engine)
    with Session(engine) as session:
        session.add_all(
            [
                Tenant(id=TENANT_A, slug="alpha", name="Alpha"),
                Tenant(id=TENANT_B, slug="bravo", name="Bravo"),
                Party(
                    id=PERSON_A,
                    tenant_id=TENANT_A,
                    party_type=PartyType.person,
                    display_name="Ada Contact",
                ),
                Party(
                    id=PERSON_B,
                    tenant_id=TENANT_A,
                    party_type=PartyType.person,
                    display_name="Grace Contact",
                ),
                Party(
                    id=ORGANIZATION_A,
                    tenant_id=TENANT_A,
                    party_type=PartyType.organization,
                    display_name="Acme Networks",
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                PartyPerson(party_id=PERSON_A, first_name="Ada", last_name="Contact"),
                PartyPerson(party_id=PERSON_B, first_name="Grace", last_name="Contact"),
                PartyOrganization(party_id=ORGANIZATION_A, legal_name="Acme Networks"),
            ]
        )
        session.flush()
        yield session
    engine.dispose()


def test_customer_and_contact_are_concurrent_roles_not_identity_tables(
    db: Session, vocabulary: PartyVocabularyRegistry
) -> None:
    scope = TenantScope(TENANT_A)
    contact = assign_role(
        db,
        scope=scope,
        vocabulary=vocabulary,
        command=AssignPartyRole(PERSON_A, "contact", status=RoleStatus.ACTIVE),
    )
    customer = assign_role(
        db,
        scope=scope,
        vocabulary=vocabulary,
        command=AssignPartyRole(ORGANIZATION_A, "customer", status=RoleStatus.ACTIVE),
    )
    assert contact.party_id == PERSON_A
    assert customer.party_id == ORGANIZATION_A
    assert contact.role_type == "contact"
    assert customer.role_type == "customer"


def test_relationships_connect_exact_capacities_not_ambiguous_parties(
    db: Session, vocabulary: PartyVocabularyRegistry
) -> None:
    scope = TenantScope(TENANT_A)
    contact = assign_role(
        db,
        scope=scope,
        vocabulary=vocabulary,
        command=AssignPartyRole(PERSON_A, "contact", status=RoleStatus.ACTIVE),
    )
    customer = assign_role(
        db,
        scope=scope,
        vocabulary=vocabulary,
        command=AssignPartyRole(ORGANIZATION_A, "customer", status=RoleStatus.ACTIVE),
    )
    relationship = relate_roles(
        db,
        scope=scope,
        vocabulary=vocabulary,
        command=RelatePartyRoles(
            contact.id,
            customer.id,
            "billing_contact_for",
            status=RelationshipStatus.ACTIVE,
        ),
    )
    assert relationship.subject_role_id == contact.id
    assert relationship.object_role_id == customer.id

    with pytest.raises(PartyInvariantError, match="subject role type"):
        relate_roles(
            db,
            scope=scope,
            vocabulary=vocabulary,
            command=RelatePartyRoles(
                customer.id,
                contact.id,
                "billing_contact_for",
            ),
        )


def test_membership_is_separate_from_relationship_and_validates_party_subtypes(
    db: Session, vocabulary: PartyVocabularyRegistry
) -> None:
    membership = create_membership(
        db,
        scope=TenantScope(TENANT_A),
        vocabulary=vocabulary,
        command=CreatePartyMembership(
            PERSON_A,
            ORGANIZATION_A,
            "admin",
            access_scope={"accounts": ["read"]},
        ),
    )
    assert membership.access_scope == {"accounts": ["read"]}

    with pytest.raises(PartyInvariantError, match="undeclared access-scope"):
        create_membership(
            db,
            scope=TenantScope(TENANT_A),
            vocabulary=vocabulary,
            command=CreatePartyMembership(
                PERSON_B,
                ORGANIZATION_A,
                "admin",
                access_scope={"billing": ["write"]},
            ),
        )

    with pytest.raises(PartyInvariantError, match="Person Party"):
        create_membership(
            db,
            scope=TenantScope(TENANT_A),
            vocabulary=vocabulary,
            command=CreatePartyMembership(
                ORGANIZATION_A, ORGANIZATION_A, "admin", access_scope={}
            ),
        )


def test_shared_contact_value_is_not_identity_and_consent_is_separate(
    db: Session, vocabulary: PartyVocabularyRegistry
) -> None:
    scope = TenantScope(TENANT_A)
    first = add_contact_point(
        db,
        scope=scope,
        vocabulary=vocabulary,
        command=AddContactPoint(PERSON_A, "email", " Shared@Example.COM "),
    )
    second = add_contact_point(
        db,
        scope=scope,
        vocabulary=vocabulary,
        command=AddContactPoint(PERSON_B, "email", "shared@example.com"),
    )
    assert first.normalized_value == second.normalized_value == "shared@example.com"

    verified_at = datetime.now(UTC)
    set_contact_verification(
        db,
        scope=scope,
        contact_point_id=first.id,
        status=ContactVerificationStatus.VERIFIED,
        source="challenge",
        occurred_at=verified_at,
    )
    set_contact_consent(
        db,
        scope=scope,
        contact_point_id=first.id,
        status=ContactConsentStatus.OPTED_OUT,
        source="customer_request",
        occurred_at=verified_at,
    )
    assert first.verification_status == "verified"
    assert first.consent_status == "opted_out"
    assert first.verification_source == "challenge"
    assert first.consent_source == "customer_request"


def test_social_contact_requires_immutable_provider_identity(
    db: Session, vocabulary: PartyVocabularyRegistry
) -> None:
    with pytest.raises(PartyInvariantError, match="provider"):
        add_contact_point(
            db,
            scope=TenantScope(TENANT_A),
            vocabulary=vocabulary,
            command=AddContactPoint(PERSON_A, "social", "@ada"),
        )


def test_external_references_are_tenant_scoped_provenance(
    db: Session, vocabulary: PartyVocabularyRegistry
) -> None:
    reference = record_external_reference(
        db,
        scope=TenantScope(TENANT_A),
        command=RecordExternalReference(
            PERSON_A, "legacy_crm", "person", "crm-42", source="migration"
        ),
    )
    assert reference.party_id == PERSON_A
    assert reference.source_system == "legacy_crm"
    assert reference.external_id == "crm-42"

    with pytest.raises(PartyConflict):
        record_external_reference(
            db,
            scope=TenantScope(TENANT_A),
            command=RecordExternalReference(
                PERSON_B, "legacy_crm", "person", "crm-42", source="migration"
            ),
        )


def test_unknown_vocabulary_and_cross_tenant_party_fail_closed(
    db: Session, vocabulary: PartyVocabularyRegistry
) -> None:
    with pytest.raises(PartyInvariantError, match="not declared"):
        assign_role(
            db,
            scope=TenantScope(TENANT_A),
            vocabulary=vocabulary,
            command=AssignPartyRole(PERSON_A, "subscriber"),
        )

    foreign = uuid.uuid4()
    db.add(
        Party(
            id=foreign,
            tenant_id=TENANT_B,
            party_type=PartyType.person,
            display_name="Foreign Party",
        )
    )
    db.flush()
    with pytest.raises(PartyNotFound):
        assign_role(
            db,
            scope=TenantScope(TENANT_A),
            vocabulary=vocabulary,
            command=AssignPartyRole(foreign, "contact"),
        )


def test_ended_role_cannot_be_reactivated(
    db: Session, vocabulary: PartyVocabularyRegistry
) -> None:
    role = assign_role(
        db,
        scope=TenantScope(TENANT_A),
        vocabulary=vocabulary,
        command=AssignPartyRole(PERSON_A, "contact", status=RoleStatus.ACTIVE),
    )
    transition_role(
        db,
        scope=TenantScope(TENANT_A),
        role_id=role.id,
        status=RoleStatus.ENDED,
    )
    with pytest.raises(PartyInvariantError, match="terminal"):
        transition_role(
            db,
            scope=TenantScope(TENANT_A),
            role_id=role.id,
            status=RoleStatus.ACTIVE,
        )


def test_relationship_and_membership_lifecycles_are_owned_and_terminal(
    db: Session, vocabulary: PartyVocabularyRegistry
) -> None:
    scope = TenantScope(TENANT_A)
    contact = assign_role(
        db,
        scope=scope,
        vocabulary=vocabulary,
        command=AssignPartyRole(PERSON_A, "contact", status=RoleStatus.ACTIVE),
    )
    customer = assign_role(
        db,
        scope=scope,
        vocabulary=vocabulary,
        command=AssignPartyRole(ORGANIZATION_A, "customer", status=RoleStatus.ACTIVE),
    )
    relationship = relate_roles(
        db,
        scope=scope,
        vocabulary=vocabulary,
        command=RelatePartyRoles(
            contact.id,
            customer.id,
            "billing_contact_for",
            status=RelationshipStatus.PENDING,
        ),
    )
    transition_relationship(
        db,
        scope=scope,
        relationship_id=relationship.id,
        status=RelationshipStatus.ACTIVE,
    )
    transition_relationship(
        db,
        scope=scope,
        relationship_id=relationship.id,
        status=RelationshipStatus.ENDED,
    )
    with pytest.raises(PartyInvariantError, match="terminal"):
        transition_relationship(
            db,
            scope=scope,
            relationship_id=relationship.id,
            status=RelationshipStatus.ACTIVE,
        )

    membership = create_membership(
        db,
        scope=scope,
        vocabulary=vocabulary,
        command=CreatePartyMembership(PERSON_A, ORGANIZATION_A, "admin"),
    )
    transition_membership(
        db,
        scope=scope,
        membership_id=membership.id,
        status=MembershipStatus.ACTIVE,
    )
    transition_membership(
        db,
        scope=scope,
        membership_id=membership.id,
        status=MembershipStatus.ENDED,
    )
    with pytest.raises(PartyInvariantError, match="terminal"):
        transition_membership(
            db,
            scope=scope,
            membership_id=membership.id,
            status=MembershipStatus.ACTIVE,
        )


def test_contact_primary_and_active_state_have_one_writer(
    db: Session, vocabulary: PartyVocabularyRegistry
) -> None:
    scope = TenantScope(TENANT_A)
    first = add_contact_point(
        db,
        scope=scope,
        vocabulary=vocabulary,
        command=AddContactPoint(
            PERSON_A, "email", "first@example.com", is_primary=True
        ),
    )
    second = add_contact_point(
        db,
        scope=scope,
        vocabulary=vocabulary,
        command=AddContactPoint(
            PERSON_A, "email", "second@example.com", is_primary=True
        ),
    )
    assert first.is_primary is False
    assert second.is_primary is True

    set_contact_active(db, scope=scope, contact_point_id=second.id, active=False)
    assert second.is_active is False
    assert second.is_primary is False

    set_primary_contact(db, scope=scope, contact_point_id=first.id)
    assert first.is_primary is True


def test_role_keys_are_declared_contract_not_hardcoded_partner_logic(
    db: Session, vocabulary: PartyVocabularyRegistry
) -> None:
    with pytest.raises(PartyInvariantError, match="role_key"):
        assign_role(
            db,
            scope=TenantScope(TENANT_A),
            vocabulary=vocabulary,
            command=AssignPartyRole(ORGANIZATION_A, "partner"),
        )
    role = assign_role(
        db,
        scope=TenantScope(TENANT_A),
        vocabulary=vocabulary,
        command=AssignPartyRole(ORGANIZATION_A, "partner", role_key="technology"),
    )
    assert role.role_key == "technology"
