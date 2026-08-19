"""Typed commands and immutable values for local website composition."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from urllib.parse import urlsplit
from uuid import UUID


class SitesError(Exception):
    """Base for site-owner refusals."""


class ContractError(SitesError, ValueError):
    """A typed site value is malformed or internally inconsistent."""


class NotFound(SitesError):
    """A referenced row does not exist in the declared tenant scope."""


class Conflict(SitesError):
    """A site-owned identity or state conflicts."""


_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _required(field: str, value: str, maximum: int) -> str:
    normalized = value.strip()
    if not normalized:
        raise ContractError(f"{field} must not be empty")
    if len(normalized) > maximum:
        raise ContractError(f"{field} exceeds {maximum} characters")
    return normalized


def _optional(field: str, value: str | None, maximum: int) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > maximum:
        raise ContractError(f"{field} exceeds {maximum} characters")
    return normalized


def validate_path(path: str) -> str:
    """Return one unambiguous local absolute path or refuse it."""
    normalized = path.strip()
    parsed = urlsplit(normalized)
    segments = normalized.split("/")
    if (
        not normalized.startswith("/")
        or normalized.startswith("//")
        or "//" in normalized
        or parsed.scheme
        or parsed.netloc
        or parsed.query
        or parsed.fragment
        or any(segment in {".", ".."} for segment in segments)
    ):
        raise ContractError(f"path {path!r} must be an unambiguous local path")
    if normalized != "/" and normalized.endswith("/"):
        normalized = normalized.rstrip("/")
    return normalized


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class SeoMetadataV1:
    title: str
    description: str | None = None
    canonical_path: str | None = None
    no_index: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "title", _required("SEO title", self.title, 300))
        object.__setattr__(
            self,
            "description",
            _optional("SEO description", self.description, 1_000),
        )
        if self.canonical_path is not None:
            object.__setattr__(
                self, "canonical_path", validate_path(self.canonical_path)
            )

    def as_dict(self) -> dict[str, object]:
        return {
            "title": self.title,
            "description": self.description,
            "canonical_path": self.canonical_path,
            "no_index": self.no_index,
        }


@dataclass(frozen=True, slots=True)
class NavigationItemV1:
    label: str
    path: str
    children: tuple[NavigationItemV1, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "label", _required("navigation label", self.label, 200)
        )
        object.__setattr__(self, "path", validate_path(self.path))

    def as_dict(self) -> dict[str, object]:
        return {
            "label": self.label,
            "path": self.path,
            "children": [child.as_dict() for child in self.children],
        }


@dataclass(frozen=True, slots=True)
class RedirectRuleV1:
    source_path: str
    target_path: str
    status_code: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_path", validate_path(self.source_path))
        object.__setattr__(self, "target_path", validate_path(self.target_path))
        if self.source_path == self.target_path:
            raise ContractError("redirect source_path and target_path cannot match")
        if self.status_code not in {301, 302, 307, 308}:
            raise ContractError("redirect status_code must be 301, 302, 307 or 308")

    def as_dict(self) -> dict[str, object]:
        return {
            "source_path": self.source_path,
            "target_path": self.target_path,
            "status_code": self.status_code,
        }


@dataclass(frozen=True, slots=True)
class SitePageSnapshotV1:
    page_ref: UUID
    page_revision_ref: UUID
    path: str
    title: str
    body: str
    seo: SeoMetadataV1
    file_refs: tuple[UUID, ...] = ()
    form_refs: tuple[UUID, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", validate_path(self.path))
        object.__setattr__(self, "title", _required("page title", self.title, 300))
        object.__setattr__(self, "body", _required("page body", self.body, 500_000))
        if len(set(self.file_refs)) != len(self.file_refs):
            raise ContractError("file_refs cannot contain duplicates")
        if len(set(self.form_refs)) != len(self.form_refs):
            raise ContractError("form_refs cannot contain duplicates")

    def as_dict(self) -> dict[str, object]:
        return {
            "page_ref": str(self.page_ref),
            "page_revision_ref": str(self.page_revision_ref),
            "path": self.path,
            "title": self.title,
            "body": self.body,
            "seo": self.seo.as_dict(),
            "file_refs": [str(value) for value in self.file_refs],
            "form_refs": [str(value) for value in self.form_refs],
        }


def _navigation_paths(items: tuple[NavigationItemV1, ...]) -> tuple[str, ...]:
    return tuple(
        path
        for item in items
        for path in (item.path, *_navigation_paths(item.children))
    )


@dataclass(frozen=True, slots=True)
class SiteReleaseV1:
    site_ref: UUID
    site_revision_ref: UUID
    pages: tuple[SitePageSnapshotV1, ...]
    navigation: tuple[NavigationItemV1, ...]
    redirects: tuple[RedirectRuleV1, ...]
    seo: SeoMetadataV1
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ContractError("SiteReleaseV1 schema_version must be 1")
        if not self.pages:
            raise ContractError("site release requires a home page")
        paths = [page.path for page in self.pages]
        page_refs = [page.page_ref for page in self.pages]
        if paths.count("/") != 1:
            raise ContractError("site release requires exactly one home path")
        if len(set(paths)) != len(paths):
            raise ContractError("site release contains a duplicate page path")
        if len(set(page_refs)) != len(page_refs):
            raise ContractError("site release contains a duplicate page identity")

        navigation_paths = _navigation_paths(self.navigation)
        if len(set(navigation_paths)) != len(navigation_paths):
            raise ContractError("navigation contains a duplicate path")
        missing_navigation = sorted(set(navigation_paths) - set(paths))
        if missing_navigation:
            raise ContractError(
                f"navigation references missing page paths: {missing_navigation}"
            )

        redirect_sources = [rule.source_path for rule in self.redirects]
        if len(set(redirect_sources)) != len(redirect_sources):
            raise ContractError("redirect contains a duplicate source path")
        shadowed = sorted(set(redirect_sources) & set(paths))
        if shadowed:
            raise ContractError(f"redirect shadows a page path: {shadowed}")
        missing_targets = sorted(
            {rule.target_path for rule in self.redirects} - set(paths)
        )
        if missing_targets:
            raise ContractError(
                f"redirect targets missing page paths: {missing_targets}"
            )

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "site_ref": str(self.site_ref),
            "site_revision_ref": str(self.site_revision_ref),
            "pages": [page.as_dict() for page in self.pages],
            "navigation": [item.as_dict() for item in self.navigation],
            "redirects": [rule.as_dict() for rule in self.redirects],
            "seo": self.seo.as_dict(),
        }

    @property
    def digest(self) -> str:
        return _digest(self.as_dict())


@dataclass(frozen=True, slots=True)
class CreateSite:
    slug: str
    name: str
    created_by_ref: UUID

    def __post_init__(self) -> None:
        slug = self.slug.strip()
        if not _SLUG.fullmatch(slug):
            raise ContractError("site slug must be lowercase kebab-case")
        object.__setattr__(self, "slug", slug)
        object.__setattr__(self, "name", _required("site name", self.name, 200))


@dataclass(frozen=True, slots=True)
class CreatePage:
    site_id: UUID
    page_key: str
    created_by_ref: UUID

    def __post_init__(self) -> None:
        key = self.page_key.strip()
        if not _SLUG.fullmatch(key):
            raise ContractError("page_key must be lowercase kebab-case")
        object.__setattr__(self, "page_key", key)


@dataclass(frozen=True, slots=True)
class CreatePageRevision:
    page_id: UUID
    title: str
    body: str
    seo: SeoMetadataV1
    created_by_ref: UUID
    file_refs: tuple[UUID, ...] = ()
    form_refs: tuple[UUID, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "title", _required("page title", self.title, 300))
        object.__setattr__(self, "body", _required("page body", self.body, 500_000))
        if len(set(self.file_refs)) != len(self.file_refs):
            raise ContractError("file_refs cannot contain duplicates")
        if len(set(self.form_refs)) != len(self.form_refs):
            raise ContractError("form_refs cannot contain duplicates")

    def content_payload(self) -> dict[str, object]:
        return {
            "title": self.title,
            "body": self.body,
            "seo": self.seo.as_dict(),
            "file_refs": [str(value) for value in self.file_refs],
            "form_refs": [str(value) for value in self.form_refs],
        }

    @property
    def content_digest(self) -> str:
        return _digest(self.content_payload())


@dataclass(frozen=True, slots=True)
class SitePageSelection:
    page_revision_id: UUID
    path: str
    sort_order: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", validate_path(self.path))
        if self.sort_order < 0:
            raise ContractError("site page sort_order must not be negative")


@dataclass(frozen=True, slots=True)
class CreateSiteRevision:
    site_id: UUID
    pages: tuple[SitePageSelection, ...]
    navigation: tuple[NavigationItemV1, ...]
    redirects: tuple[RedirectRuleV1, ...]
    seo: SeoMetadataV1
    created_by_ref: UUID

    def __post_init__(self) -> None:
        if not self.pages:
            raise ContractError("site revision requires at least one page")
        revisions = [selection.page_revision_id for selection in self.pages]
        orders = [selection.sort_order for selection in self.pages]
        if len(set(revisions)) != len(revisions):
            raise ContractError("site revision contains a duplicate page revision")
        if len(set(orders)) != len(orders):
            raise ContractError("site revision contains a duplicate sort_order")


__all__ = [
    "Conflict",
    "ContractError",
    "CreatePage",
    "CreatePageRevision",
    "CreateSite",
    "CreateSiteRevision",
    "NavigationItemV1",
    "NotFound",
    "RedirectRuleV1",
    "SeoMetadataV1",
    "SitePageSelection",
    "SitePageSnapshotV1",
    "SiteReleaseV1",
    "SitesError",
    "validate_path",
]
