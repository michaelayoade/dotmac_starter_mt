"""Product-neutral tenant website composition owner."""

from dotmac_sites.contracts import (
    Conflict,
    ContractError,
    CreatePage,
    CreatePageRevision,
    CreateSite,
    CreateSiteRevision,
    NavigationItemV1,
    NotFound,
    RedirectRuleV1,
    SeoMetadataV1,
    SitePageSelection,
    SitePageSnapshotV1,
    SiteReleaseV1,
    SitesError,
    validate_path,
)
from dotmac_sites.lifecycle import (
    SiteRevisionState,
    SiteState,
    TransitionError,
    check_revision_transition,
    check_site_transition,
)
from dotmac_sites.manifest import module
from dotmac_sites.migrations import versions_dir
from dotmac_sites.models import Page, PageRevision, Site, SiteRevision, SiteRevisionPage
from dotmac_sites.service import (
    archive_site,
    create_page,
    create_page_revision,
    create_site,
    create_site_revision,
    get_ready_release,
    mark_site_revision_ready,
)

__version__ = "0.1.0a1"

__all__ = [
    "Conflict",
    "ContractError",
    "CreatePage",
    "CreatePageRevision",
    "CreateSite",
    "CreateSiteRevision",
    "NavigationItemV1",
    "NotFound",
    "Page",
    "PageRevision",
    "RedirectRuleV1",
    "SeoMetadataV1",
    "Site",
    "SitePageSelection",
    "SitePageSnapshotV1",
    "SiteReleaseV1",
    "SiteRevision",
    "SiteRevisionPage",
    "SiteRevisionState",
    "SiteState",
    "SitesError",
    "TransitionError",
    "__version__",
    "archive_site",
    "check_revision_transition",
    "check_site_transition",
    "create_page",
    "create_page_revision",
    "create_site",
    "create_site_revision",
    "get_ready_release",
    "mark_site_revision_ready",
    "module",
    "validate_path",
    "versions_dir",
]
