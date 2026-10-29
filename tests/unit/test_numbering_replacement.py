"""Pure replacement contracts; PostgreSQL proofs live beside the owner canaries."""

from dataclasses import FrozenInstanceError

import pytest
from dotmac_numbering import CollisionDomain, NumberingError, SeriesConfiguration
from dotmac_numbering.models import NumberClaim, PlatformNumberClaim
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.schema import CreateColumn


def test_domain_policy_is_explicit_and_immutable():
    domain = CollisionDomain(code="ar.invoice", partitioned=True)
    domain.validate()
    with pytest.raises(FrozenInstanceError):
        domain.code = "other"  # type: ignore[misc]


@pytest.mark.parametrize("code", ["", "x" * 81])
def test_domain_code_has_a_bounded_identity(code):
    with pytest.raises(NumberingError):
        CollisionDomain(code=code).validate()


def test_exact_partition_policy_refuses_missing_or_invented_partitions():
    assert CollisionDomain(code="quote").partition_key(None) == ""
    with pytest.raises(NumberingError):
        CollisionDomain(code="quote").partition_key("invented")
    domain = CollisionDomain(code="bank", partitioned=True)
    with pytest.raises(NumberingError):
        domain.partition_key(None)
    assert domain.partition_key("bank-account-42") == "bank-account-42"


@pytest.mark.parametrize(
    "field,size", [("prefix", 33), ("suffix", 33), ("separator", 9)]
)
def test_strict_configuration_rejects_database_field_overflow(field, size):
    with pytest.raises(NumberingError):
        SeriesConfiguration(series_code="new", **{field: "x" * size}).validate()


@pytest.mark.parametrize("model", [NumberClaim, PlatformNumberClaim])
@pytest.mark.parametrize(
    "field", ["partition_key", "formatted_number", "document_reference"]
)
def test_claim_equality_collation_compiles_for_each_supported_dialect(model, field):
    column = model.__table__.c[field]
    postgres_ddl = str(CreateColumn(column).compile(dialect=postgresql.dialect()))
    sqlite_ddl = str(CreateColumn(column).compile(dialect=sqlite.dialect()))
    assert 'COLLATE "C"' in postgres_ddl
    assert 'COLLATE "BINARY"' in sqlite_ddl
    assert 'COLLATE "C"' not in sqlite_ddl
