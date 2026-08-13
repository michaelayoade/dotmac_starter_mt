"""The parties list runs on `dotmac_kernel.listing` — the first cutover.

`app/features/parties` is the reference assembly's proof that the ported list
contract survives a real service + route + template stack. It is NOT the second
independent consumer ADR-0006 § 5 requires: the starter owns the kernel, so
adopting it here cannot close that gate. What this does is de-risk the Sub and
ERP cutovers — every defect found here is one they do not hit.

Three behaviours are worth pinning, because each was previously a place the
route, the count and the template could disagree:

1. an undeclared sort field or page size fails LOUDLY instead of silently
   falling back to a different ordering;
2. a page past the end is clamped and the URL is corrected, rather than
   rendering an empty table while the address bar still claims page 99;
3. a pagination link carries the WHOLE query state, so Next cannot drop a
   filter the template's old hand-built query string forgot to concatenate.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest
from dotmac_kernel.listing import PageMeta
from dotmac_kernel.models import Tenant
from sqlalchemy.orm import Session

from app.features.parties import service as parties_service
from app.features.parties.schemas import PersonPartyCreate

PARTY_LIST = parties_service.PARTY_LIST


def _make(db: Session, tenant: Tenant, n: int) -> None:
    for i in range(n):
        parties_service.create_person_party(
            db,
            tenant,
            PersonPartyCreate(
                email=f"p{i:03d}@example.com", first_name=f"P{i:03d}", last_name="Test"
            ),
        )


def test_the_definition_declares_the_lists_real_capabilities() -> None:
    assert PARTY_LIST.key == "parties"
    assert PARTY_LIST.searchable_keys == ("search",)
    assert PARTY_LIST.filterable_keys == ("party_type",)
    assert PARTY_LIST.sortable_keys == ("display_name", "created_at")
    assert PARTY_LIST.default_sort == "created_at"
    assert PARTY_LIST.default_sort_dir == "desc"


def test_an_undeclared_sort_or_page_size_is_rejected_not_silently_ignored() -> None:
    """A stale bookmark for a removed column must not quietly re-sort the page."""
    with pytest.raises(ValueError, match="Unsupported sort field"):
        PARTY_LIST.build_query(search=None, filters={}, sort_by="email")
    with pytest.raises(ValueError, match="per_page must be one of"):
        PARTY_LIST.build_query(search=None, filters={}, per_page=13)
    with pytest.raises(ValueError, match="Unsupported filters"):
        PARTY_LIST.build_query(search=None, filters={"tenant_id": "x"})


def test_an_unknown_value_for_a_declared_filter_degrades_to_no_filter(
    db: Session, tenant_row: Tenant
) -> None:
    """A declared filter NAME with a junk VALUE is a stale bookmark, not a bug.

    Deliberately different from the case above: rejecting the name protects the
    contract; rejecting every value would 400 a user whose bookmark predates an
    enum change.
    """
    _make(db, tenant_row, 3)
    query = PARTY_LIST.build_query(search=None, filters={"party_type": "martian"})

    assert parties_service.count_parties(db, query) == 3


def test_sort_direction_is_honoured_in_both_directions(
    db: Session, tenant_row: Tenant
) -> None:
    _make(db, tenant_row, 3)

    ascending = parties_service.search_parties(
        db,
        PARTY_LIST.build_query(
            search=None, filters={}, sort_by="display_name", sort_dir="asc"
        ),
    )
    descending = parties_service.search_parties(
        db,
        PARTY_LIST.build_query(
            search=None, filters={}, sort_by="display_name", sort_dir="desc"
        ),
    )

    names = [p.display_name for p in ascending]
    assert names == sorted(names)
    assert [p.display_name for p in descending] == list(reversed(names))


def test_the_count_and_the_page_agree_because_they_share_one_filter(
    db: Session, tenant_row: Tenant
) -> None:
    _make(db, tenant_row, 7)
    query = PARTY_LIST.build_query(search="P00", filters={}, per_page=10)

    rows = parties_service.search_parties(db, query)
    total = parties_service.count_parties(db, query)

    assert total == 7
    assert len(rows) == 7
    assert PageMeta.from_query(query, total).total_pages == 1


def test_page_meta_clamps_a_page_past_the_end(db: Session, tenant_row: Tenant) -> None:
    """Previously this rendered an empty table at 200 and kept `page=99`."""
    _make(db, tenant_row, 5)
    query = PARTY_LIST.build_query(search=None, filters={}, page=99, per_page=10)

    meta = PageMeta.from_query(query, parties_service.count_parties(db, query))

    assert meta.page == 1
    assert meta.total_pages == 1
    assert (meta.start_item, meta.end_item) == (1, 5)


def test_a_pagination_url_carries_the_whole_query_state() -> None:
    """The old template concatenated only `q` and `party_type` by hand."""
    query = PARTY_LIST.build_query(
        search="acme",
        filters={"party_type": "organization"},
        sort_by="display_name",
        sort_dir="asc",
        page=1,
        per_page=50,
    )

    params = parse_qs(urlsplit(query.url("/admin/parties", page=2)).query)

    assert params == {
        "search": ["acme"],
        "party_type": ["organization"],
        "sort": ["display_name"],
        "dir": ["asc"],
        "page": ["2"],
        "per_page": ["50"],
    }


def test_paging_walks_the_whole_result_set_without_gaps_or_repeats(
    db: Session, tenant_row: Tenant
) -> None:
    """`offset` is derived once, by the contract, instead of at each call site."""
    _make(db, tenant_row, 12)
    seen: list[str] = []
    page = 1
    while True:
        query = PARTY_LIST.build_query(
            search=None, filters={}, sort_by="display_name", sort_dir="asc", page=page
        )
        rows = parties_service.search_parties(db, query)
        if not rows:
            break
        seen.extend(str(p.id) for p in rows)
        meta = PageMeta.from_query(query, parties_service.count_parties(db, query))
        if not meta.has_next:
            break
        page += 1

    assert len(seen) == 12
    assert len(set(seen)) == 12
