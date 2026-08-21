"""Canaries for the immutable provider-neutral site release value."""

from __future__ import annotations

from importlib import import_module
from types import ModuleType
from uuid import UUID

import pytest

SITE_ID = UUID("10000000-0000-0000-0000-000000000001")
SITE_REVISION_ID = UUID("10000000-0000-0000-0000-000000000002")
HOME_ID = UUID("10000000-0000-0000-0000-000000000003")
HOME_REVISION_ID = UUID("10000000-0000-0000-0000-000000000004")
ABOUT_ID = UUID("10000000-0000-0000-0000-000000000005")
ABOUT_REVISION_ID = UUID("10000000-0000-0000-0000-000000000006")


def _contracts() -> ModuleType:
    try:
        return import_module("dotmac_sites.contracts")
    except ModuleNotFoundError as exc:
        if not (exc.name or "").startswith("dotmac_sites"):
            raise
        pytest.fail(
            "dotmac-sites is intentionally absent: this is the Gate 1 RED canary"
        )


def _page(contracts: ModuleType, *, about: bool = False) -> object:
    return contracts.SitePageSnapshotV1(
        page_ref=ABOUT_ID if about else HOME_ID,
        page_revision_ref=ABOUT_REVISION_ID if about else HOME_REVISION_ID,
        path="/about" if about else "/",
        title="About" if about else "Home",
        body="About Dotmac" if about else "Welcome to Dotmac",
        seo=contracts.SeoMetadataV1(
            title="About Dotmac" if about else "Dotmac",
            description=None,
            canonical_path="/about" if about else "/",
            no_index=False,
        ),
        file_refs=(),
        form_refs=(),
    )


def _release(contracts: ModuleType, *, reverse: bool = False) -> object:
    pages = (_page(contracts), _page(contracts, about=True))
    if reverse:
        pages = tuple(reversed(pages))
    return contracts.SiteReleaseV1(
        site_ref=SITE_ID,
        site_revision_ref=SITE_REVISION_ID,
        pages=pages,
        navigation=(
            contracts.NavigationItemV1(label="Home", path="/"),
            contracts.NavigationItemV1(label="About", path="/about"),
        ),
        redirects=(
            contracts.RedirectRuleV1(
                source_path="/company", target_path="/about", status_code=301
            ),
        ),
        seo=contracts.SeoMetadataV1(
            title="Dotmac", description="Connectivity", canonical_path="/"
        ),
    )


@pytest.mark.parametrize(
    "path",
    ["", "about", "//about", "/a//b", "/a/../b", "/a/./b", "/a?x=1", "/a#x"],
)
def test_paths_refuse_non_local_or_ambiguous_values(path: str) -> None:
    contracts = _contracts()
    with pytest.raises(contracts.ContractError, match="path"):
        contracts.validate_path(path)


def test_release_requires_one_home_page_and_unique_routes() -> None:
    contracts = _contracts()
    home = _page(contracts)
    about = _page(contracts, about=True)
    with pytest.raises(contracts.ContractError, match="home"):
        contracts.SiteReleaseV1(
            site_ref=SITE_ID,
            site_revision_ref=SITE_REVISION_ID,
            pages=(about,),
            navigation=(),
            redirects=(),
            seo=contracts.SeoMetadataV1(title="Dotmac"),
        )
    with pytest.raises(contracts.ContractError, match="duplicate.*path"):
        contracts.SiteReleaseV1(
            site_ref=SITE_ID,
            site_revision_ref=SITE_REVISION_ID,
            pages=(home, home),
            navigation=(),
            redirects=(),
            seo=contracts.SeoMetadataV1(title="Dotmac"),
        )


def test_navigation_must_point_to_a_page_in_the_same_snapshot() -> None:
    contracts = _contracts()
    with pytest.raises(contracts.ContractError, match="navigation"):
        contracts.SiteReleaseV1(
            site_ref=SITE_ID,
            site_revision_ref=SITE_REVISION_ID,
            pages=(_page(contracts),),
            navigation=(contracts.NavigationItemV1(label="Missing", path="/missing"),),
            redirects=(),
            seo=contracts.SeoMetadataV1(title="Dotmac"),
        )


def test_redirects_are_local_non_shadowing_and_use_a_safe_status() -> None:
    contracts = _contracts()
    with pytest.raises(contracts.ContractError, match="redirect.*page"):
        contracts.SiteReleaseV1(
            site_ref=SITE_ID,
            site_revision_ref=SITE_REVISION_ID,
            pages=(_page(contracts),),
            navigation=(),
            redirects=(
                contracts.RedirectRuleV1(
                    source_path="/", target_path="/about", status_code=301
                ),
            ),
            seo=contracts.SeoMetadataV1(title="Dotmac"),
        )
    with pytest.raises(contracts.ContractError, match="status_code"):
        contracts.RedirectRuleV1(source_path="/old", target_path="/", status_code=200)


def test_release_digest_is_deterministic_complete_and_order_sensitive() -> None:
    contracts = _contracts()
    first = _release(contracts)
    second = _release(contracts)
    reordered = _release(contracts, reverse=True)
    assert first.digest == second.digest
    assert first.as_dict() == second.as_dict()
    assert first.digest != reordered.digest
    assert first.schema_version == 1


def test_opaque_file_and_form_references_survive_the_release_value() -> None:
    contracts = _contracts()
    file_ref = UUID("20000000-0000-0000-0000-000000000001")
    form_ref = UUID("20000000-0000-0000-0000-000000000002")
    page = contracts.SitePageSnapshotV1(
        page_ref=HOME_ID,
        page_revision_ref=HOME_REVISION_ID,
        path="/",
        title="Home",
        body="Welcome",
        seo=contracts.SeoMetadataV1(title="Dotmac"),
        file_refs=(file_ref,),
        form_refs=(form_ref,),
    )
    release = contracts.SiteReleaseV1(
        site_ref=SITE_ID,
        site_revision_ref=SITE_REVISION_ID,
        pages=(page,),
        navigation=(),
        redirects=(),
        seo=contracts.SeoMetadataV1(title="Dotmac"),
    )
    assert release.as_dict()["pages"][0]["file_refs"] == [str(file_ref)]
    assert release.as_dict()["pages"][0]["form_refs"] == [str(form_ref)]
