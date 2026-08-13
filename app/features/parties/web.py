"""Parties' web (HTMX) surface: `/admin/parties` list/create/detail/delete/edit.

Mirrors `app.features.auth.web`/`app.features.web.web`'s established shape —
`require_web_auth` on every route, thin wrappers (no direct DB query in this
file; all of that lives in `app.features.parties.service`), `render()` for
every HTML response.

HTMX fragment-vs-full convention (ported from
`SUB:templates/admin/customers/index.html`+`_table.html`, per the task
brief): `GET /admin/parties` returns `admin/parties/_table.html` (the table
only) when the request carries an `HX-Request` header — every search/filter/
pagination interaction on the index page is an `hx-get` back to this same
route, threading `q`/`party_type`/`page` through query params — and the full
`admin/parties/index.html` page (shell + filters + the same table, included
once) otherwise.

Create success and delete both redirect the same way `app.features.auth.web.
login_submit` does: a real 302 PLUS an additive `HX-Redirect` header, so a
plain (non-htmx) client just follows the 302 and an htmx client (the
`hx-post` forms/buttons these templates use) does a full browser navigation
instead of trying to swap a redirect's followed body into the triggering
element. A validation failure on create re-renders `create.html` at 200 with
field errors — same "re-render, don't redirect" convention as
`app.features.auth.web.login_submit`'s bad-credentials path.

`GET`/`POST /admin/parties/{id}/edit` (Task 5) follow the identical
re-render-on-failure / HX-Redirect-on-success shape, but there is only ONE
form per party (type-appropriate — person or organization — determined by
the already-persisted `party.party_type`, never a user-supplied value), not
a tab-toggle between two: `party_type` is immutable, so the edit screen
never offers a choice. `parties_service.update_person_party`/
`update_organization_party` are the single write-owner of the recomputed
`display_name` projection (see docs/ARCHITECTURE.md's "Known dual-writer:
Parties" section) — this router only validates, authorizes (via
`require_web_auth`), and delegates.
"""

from __future__ import annotations

from uuid import UUID

from dotmac_kernel.deps import get_db, require_tenant
from dotmac_kernel.exceptions import ConflictError
from dotmac_kernel.listing import PageMeta, request_needs_canonicalization
from dotmac_kernel.models import Party, PartyType, Tenant
from dotmac_kernel.templating import render
from dotmac_kernel.web_deps import require_web_auth
from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.features.parties import service as parties_service
from app.features.parties.schemas import (
    OrganizationPartyCreate,
    OrganizationPartyUpdate,
    PersonPartyCreate,
    PersonPartyUpdate,
)

router = APIRouter(prefix="/admin/parties", tags=["web"])

#: The list's page size, sort and filter vocabulary now live in
#: `parties_service.PARTY_LIST`. The old module-level `PAGE_SIZE` constant and
#: the `_party_type_filter` parser are gone: the definition owns the page-size
#: options, and the service owns turning a declared filter's VALUE into an enum.
_VALID_TABS = {"person", "organization"}


def _field_errors(exc: ValidationError) -> dict[str, str]:
    errors: dict[str, str] = {}
    for error in exc.errors():
        loc = error.get("loc", ())
        field = str(loc[0]) if loc else "_form"
        errors[field] = str(error.get("msg", "Invalid value"))
    return errors


def _render_create_form(
    request: Request,
    *,
    active_tab: str,
    errors: dict[str, dict[str, str]] | None = None,
    form: dict[str, str] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    return render(
        request,
        "admin/parties/create.html",
        {
            "active_nav": "parties",
            "page_title": "New Party",
            "party_type": active_tab if active_tab in _VALID_TABS else "person",
            "errors": errors or {},
            "form": form or {},
        },
        status_code=status_code,
    )


def _render_edit_form(
    request: Request,
    *,
    party: Party,
    errors: dict[str, str] | None = None,
    form: dict[str, str] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    return render(
        request,
        "admin/parties/edit.html",
        {
            "active_nav": "parties",
            "page_title": f"Edit {party.display_name}",
            "party": party,
            "errors": errors or {},
            "form": form or {},
        },
        status_code=status_code,
    )


@router.get("")
def index(
    request: Request,
    q: str | None = None,
    party_type: str | None = None,
    sort: str | None = None,
    dir: str | None = None,
    page: int = Query(default=1, ge=1),
    per_page: int | None = Query(default=None),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    auth: dict = Depends(require_web_auth),
) -> HTMLResponse | RedirectResponse:
    """The parties list, normalized by `parties_service.PARTY_LIST`.

    This route validates, authorizes and delegates: it does not decide what is
    sortable, what the page size may be, or how a page number becomes an
    offset. An undeclared sort field or page size raises `ValueError` from
    `build_query`, which the error middleware renders as a 400 — a stale
    bookmark for a column that no longer exists fails loudly instead of
    silently falling back to a different ordering.

    A page past the end used to render an empty table while the URL still
    claimed page 99. `PageMeta` clamps it and, on a full-page load,
    `request_needs_canonicalization` turns that into a redirect to the URL that
    actually describes what is on screen. The HTMX branch does not redirect —
    an `hx-get` swaps a fragment, and the client already pushed the URL.
    """
    list_query = parties_service.PARTY_LIST.build_query(
        search=q,
        filters={"party_type": party_type},
        sort_by=sort,
        sort_dir=dir,
        page=page,
        per_page=per_page,
    )
    total = parties_service.count_parties(db, list_query)
    page_meta = PageMeta.from_query(list_query, total)
    if page_meta.page != list_query.page:
        list_query = list_query.with_page(page_meta.page)

    is_htmx = bool(request.headers.get("HX-Request"))
    if not is_htmx and request_needs_canonicalization(
        list_query,
        search=q,
        filters={"party_type": party_type},
        sort_by=sort,
        sort_dir=dir,
        page=page,
        per_page=per_page,
    ):
        return RedirectResponse(
            list_query.url(str(request.url.path)), status_code=status.HTTP_303_SEE_OTHER
        )

    context = {
        "parties": parties_service.search_parties(db, list_query),
        "list_query": list_query,
        "page_meta": page_meta,
        "q": list_query.search or "",
        "party_type": list_query.filter_value("party_type") or "",
    }
    if is_htmx:
        return render(request, "admin/parties/_table.html", context)
    context.update({"active_nav": "parties", "page_title": "Parties"})
    return render(request, "admin/parties/index.html", context)


@router.get("/create")
def create_form(
    request: Request,
    party_type: str = "person",
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    auth: dict = Depends(require_web_auth),
) -> HTMLResponse:
    return _render_create_form(request, active_tab=party_type)


@router.post("/people", response_model=None)
async def create_person(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    auth: dict = Depends(require_web_auth),
) -> HTMLResponse | RedirectResponse:
    form_data = await request.form()
    raw = {
        "first_name": str(form_data.get("first_name", "")).strip(),
        "last_name": str(form_data.get("last_name", "")).strip(),
        "email": str(form_data.get("email", "")).strip(),
    }
    try:
        payload = PersonPartyCreate(**raw)
    except ValidationError as exc:
        return _render_create_form(
            request,
            active_tab="person",
            errors={"person": _field_errors(exc)},
            form=raw,
        )

    try:
        party = parties_service.create_person_party(db, tenant, payload)
    except ConflictError as exc:
        return _render_create_form(
            request,
            active_tab="person",
            errors={"person": {"_form": str(exc)}},
            form=raw,
        )

    detail_url = f"/admin/parties/{party.id}"
    response = RedirectResponse(url=detail_url, status_code=302)
    response.headers["HX-Redirect"] = detail_url
    return response


@router.post("/organizations", response_model=None)
async def create_organization(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    auth: dict = Depends(require_web_auth),
) -> HTMLResponse | RedirectResponse:
    form_data = await request.form()
    raw = {
        "legal_name": str(form_data.get("legal_name", "")).strip(),
        "email": str(form_data.get("email", "")).strip(),
    }
    try:
        payload = OrganizationPartyCreate(
            legal_name=raw["legal_name"], email=raw["email"] or None
        )
    except ValidationError as exc:
        return _render_create_form(
            request,
            active_tab="organization",
            errors={"organization": _field_errors(exc)},
            form=raw,
        )

    try:
        party = parties_service.create_organization_party(db, tenant, payload)
    except ConflictError as exc:
        return _render_create_form(
            request,
            active_tab="organization",
            errors={"organization": {"_form": str(exc)}},
            form=raw,
        )

    detail_url = f"/admin/parties/{party.id}"
    response = RedirectResponse(url=detail_url, status_code=302)
    response.headers["HX-Redirect"] = detail_url
    return response


@router.get("/{party_id}")
def detail(
    request: Request,
    party_id: UUID,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    auth: dict = Depends(require_web_auth),
) -> HTMLResponse:
    party = parties_service.get_party(db, party_id)
    return render(
        request,
        "admin/parties/detail.html",
        {"active_nav": "parties", "page_title": party.display_name, "party": party},
    )


@router.get("/{party_id}/edit")
def edit_form(
    request: Request,
    party_id: UUID,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    auth: dict = Depends(require_web_auth),
) -> HTMLResponse:
    party = parties_service.get_party(db, party_id)
    return _render_edit_form(request, party=party)


@router.post("/{party_id}/edit", response_model=None)
async def edit_submit(
    request: Request,
    party_id: UUID,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    auth: dict = Depends(require_web_auth),
) -> HTMLResponse | RedirectResponse:
    party = parties_service.get_party(db, party_id)
    form_data = await request.form()

    if party.party_type == PartyType.person:
        raw = {
            "first_name": str(form_data.get("first_name", "")).strip(),
            "last_name": str(form_data.get("last_name", "")).strip(),
            "email": str(form_data.get("email", "")).strip(),
        }
        try:
            person_payload = PersonPartyUpdate(
                first_name=raw["first_name"],
                last_name=raw["last_name"],
                email=raw["email"] or None,
            )
        except ValidationError as exc:
            return _render_edit_form(
                request, party=party, errors=_field_errors(exc), form=raw
            )
        try:
            parties_service.update_person_party(db, party_id, person_payload)
        except ConflictError as exc:
            return _render_edit_form(
                request, party=party, errors={"_form": str(exc)}, form=raw
            )
    else:
        raw = {
            "legal_name": str(form_data.get("legal_name", "")).strip(),
            "email": str(form_data.get("email", "")).strip(),
        }
        try:
            organization_payload = OrganizationPartyUpdate(
                legal_name=raw["legal_name"], email=raw["email"] or None
            )
        except ValidationError as exc:
            return _render_edit_form(
                request, party=party, errors=_field_errors(exc), form=raw
            )
        try:
            parties_service.update_organization_party(
                db, party_id, organization_payload
            )
        except ConflictError as exc:
            return _render_edit_form(
                request, party=party, errors={"_form": str(exc)}, form=raw
            )

    detail_url = f"/admin/parties/{party_id}"
    response = RedirectResponse(url=detail_url, status_code=302)
    response.headers["HX-Redirect"] = detail_url
    return response


@router.post("/{party_id}/delete", response_model=None)
def delete(
    request: Request,
    party_id: UUID,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    auth: dict = Depends(require_web_auth),
) -> RedirectResponse:
    parties_service.delete_party(db, party_id)
    response = RedirectResponse(url="/admin/parties", status_code=302)
    response.headers["HX-Redirect"] = "/admin/parties"
    return response


__all__ = ["router"]
