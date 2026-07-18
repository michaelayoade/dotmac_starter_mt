# Display/Locale Settings Domain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tenant-configurable display behavior — timezone and date/datetime formats — as settings-as-data, consumed by the admin portal at render time (user rule: "everything by settings, datetime etc all").

**Architecture:** A new `display` setting domain (three specs: `timezone`, `date_format`, `datetime_format`) resolved once per request via the exact seam branding uses (`get_request_display` memoized on `request.state.display`, warmed in `require_web_auth`), consumed by two new `@pass_context` Jinja filters (`local_datetime`, `local_date`) that convert UTC-aware model timestamps to the tenant's timezone/format. Write-path validation gets a per-spec `validator` hook on `SettingSpec` (zoneinfo lookup for timezone, strftime probe for formats); the read path silently degrades to spec default on a bad legacy row, and the filters fail safe (UTC + default formats) when `request.state.display` was never warmed (error pages, unauthenticated renders). The JSON API is untouched — responses stay ISO-8601 UTC; display formatting is a portal-only concern.

**Tech Stack:** stdlib `zoneinfo` (Python ≥3.12 — no new dependency), Jinja2 `pass_context` filters, existing settings resolver/registry, Alembic (check-constraint widening).

## Global Constraints

- All governance suites green at every commit; unit baseline ~437, integration 43. Integration via `TEST_DB_PORT=5437 make test-db-up && TEST_DB_PORT=5437 make test-integration` (5433/5434 are production — never touch).
- USER RULES: everything by settings (no hardcoded display behavior anywhere a template could reach); SoT rubric (the spec registry is the SoT for display knobs — the filters are the ONLY consumer path; no parallel format constants); template framing (this is a reusable-starter feature, not fleet-specific).
- New spec keys MUST have quoted-literal readers outside `app/features/settings/` and `app/core/settings_resolver.py` (no-orphan-settings gate) — `app/core/display.py` provides them.
- API JSON serialization unchanged (ISO-8601 UTC) — pin nothing, change nothing there; document the boundary.
- Version: minor bump → **0.7.0** (new domain + migration + new manifest-visible behavior, no breaking change).
- Branch: `display-settings` off main. PR-to-green-then-merge finish (`gh workflow run ci.yml --ref display-settings` — event triggers are unreliable on this repo).

## File Structure

- Modify `app/core/settings_models.py` — `SettingDomain.display` enum member (the check constraint derives from the enum; verify, see T1).
- Create `alembic/versions/20260718_0006_display_setting_domain.py` — widen `ck_domain_settings_domain`.
- Modify `app/core/settings_resolver.py` — `SettingSpec.validator` field; enforce in `validate_spec_value` (loud) and `resolve_with_source` (silent degrade to default).
- Modify `app/features/settings/spec.py` — three `display` specs + two validator functions.
- Create `app/core/display.py` — `DisplaySettings`, `load_display`, `get_request_display` (mirrors `app/core/branding.py`).
- Modify `app/core/templating.py` — register `local_datetime`/`local_date` filters.
- Modify `app/core/web_deps.py` — warm `get_request_display` in `require_web_auth` beside `get_request_branding`.
- Modify `templates/admin/rbac/grants.html`, `templates/admin/rbac/_audit_table.html` — the only two raw timestamp renders.
- Create `tests/unit/test_display_settings.py`; modify `tests/architecture/test_web_conventions.py` (raw-`*_at` governance), `tests/unit/test_settings_service.py` region if needed.
- Docs: `CLAUDE.md`, `docs/ARCHITECTURE.md`, `CHANGELOG.md`, `docs/superpowers/phase2-backlog.md`.

---

### Task 1: `display` domain — enum + migration + specs with write-time validators

**Files:**
- Modify: `app/core/settings_models.py` (SettingDomain enum)
- Create: `alembic/versions/20260718_0006_display_setting_domain.py`
- Modify: `app/core/settings_resolver.py` (SettingSpec + validate_spec_value + resolve_with_source)
- Modify: `app/features/settings/spec.py`
- Test: `tests/unit/test_display_settings.py` (new), existing settings suites stay green

**Interfaces:**
- Produces: `SettingDomain.display`; specs `("display","timezone"|"date_format"|"datetime_format")` with defaults `"UTC"`, `"%Y-%m-%d"`, `"%Y-%m-%d %H:%M"`; `SettingSpec.validator: Callable[[object], None] | None` (raises `ValueError` on bad value). Task 2 consumes the three keys by quoted literal.

- [ ] **Step 1: Failing tests first** — in `tests/unit/test_display_settings.py`:

```python
"""Display settings domain: spec registration + validator behavior.

Write path (update_setting/validate_spec_value) rejects loudly; read path
(resolve_value) silently degrades a bad stored row to the spec default —
same split the resolver already applies to allowed/min/max violations.
"""
from __future__ import annotations

import pytest

from app.core.exceptions import BadRequestError
from app.core.settings_models import SettingDomain
from app.core.settings_resolver import (
    get_spec,
    resolve_value,
    upsert_by_key,
    validate_spec_value,
)
import app.features.settings.spec  # noqa: F401 — registration side effect


class TestDisplaySpecs:
    def test_display_specs_registered_with_expected_defaults(self) -> None:
        assert get_spec(SettingDomain.display, "timezone").default == "UTC"
        assert get_spec(SettingDomain.display, "date_format").default == "%Y-%m-%d"
        assert (
            get_spec(SettingDomain.display, "datetime_format").default
            == "%Y-%m-%d %H:%M"
        )

    def test_timezone_write_rejects_unknown_iana_name(self) -> None:
        spec = get_spec(SettingDomain.display, "timezone")
        with pytest.raises(BadRequestError):
            validate_spec_value(spec, "Mars/Olympus_Mons")

    def test_timezone_write_accepts_real_iana_name(self) -> None:
        spec = get_spec(SettingDomain.display, "timezone")
        assert validate_spec_value(spec, "Europe/London") == "Europe/London"

    def test_format_write_rejects_directive_free_string(self) -> None:
        spec = get_spec(SettingDomain.display, "date_format")
        with pytest.raises(BadRequestError):
            validate_spec_value(spec, "yyyy-mm-dd")  # no % directive

    def test_format_write_accepts_strftime_pattern(self) -> None:
        spec = get_spec(SettingDomain.display, "datetime_format")
        assert validate_spec_value(spec, "%d %b %Y %H:%M") == "%d %b %Y %H:%M"

    def test_read_path_degrades_bad_stored_timezone_to_default(
        self, db, tenant_row
    ) -> None:
        # Bypass write validation (legacy/hand-edited row) via direct upsert.
        upsert_by_key(
            db, SettingDomain.display, "timezone", "Not/AZone",
            tenant_id=tenant_row.id,
        )
        assert (
            resolve_value(
                db, SettingDomain.display, "timezone", tenant_id=tenant_row.id
            )
            == "UTC"
        )
```

- [ ] **Step 2: Run to verify RED** — `poetry run pytest tests/unit/test_display_settings.py -q` → fails: `SettingDomain` has no member `display` (AttributeError at import).

- [ ] **Step 3: Enum + verify constraint derivation** — add `display = "display"` to `SettingDomain` in `app/core/settings_models.py`. READ the model's `CheckConstraint` definition (`ck_domain_settings_domain`): if it is built from the enum members programmatically, the model side is done; if the constraint SQL is a hand-written literal list, extend it to include `'display'`. Either way the DATABASE constraint still needs the migration in Step 5 — the model-side definition only affects `create_all` databases (unit SQLite / fresh installs).

- [ ] **Step 4: `SettingSpec.validator` + enforcement** — in `app/core/settings_resolver.py`:

```python
# In the SettingSpec dataclass (keep field order — new field is last, defaulted):
    validator: Callable[[object], None] | None = None
```

(`from collections.abc import Callable` import.) In `validate_spec_value`, after the existing `allowed`/`min_value`/`max_value` checks, before returning the coerced value:

```python
    if spec.validator is not None:
        try:
            spec.validator(coerced)
        except ValueError as exc:
            raise BadRequestError(
                f"Invalid value for {spec.domain.value}.{spec.key}: {exc}"
            ) from None
```

In `resolve_with_source`, in the same place the `allowed`/range checks currently degrade a stored row to `(spec default, "default")`, apply the validator with the identical silent-degrade behavior (mirror the surrounding code style exactly — this is the read-path fail-safe, deliberately NOT loud; see module docstring's write-vs-read contrast and extend that docstring with one sentence naming `validator`).

- [ ] **Step 5: Specs + validators** — append to `app/features/settings/spec.py`:

```python
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _validate_timezone(value: object) -> None:
    try:
        ZoneInfo(str(value))
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown IANA timezone: {value!r}") from exc


def _validate_strftime(value: object) -> None:
    fmt = str(value)
    if "%" not in fmt:
        raise ValueError("format must contain at least one strftime % directive")
    try:
        datetime(2026, 1, 31, 13, 45, tzinfo=UTC).strftime(fmt)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"invalid strftime format: {exc}") from exc
```

and three specs in `SPECS` (they will auto-appear in `/admin/settings` — the index iterates `all_specs()`):

```python
    SettingSpec(
        domain=SettingDomain.display,
        key="timezone",
        value_type=SettingValueType.string,
        default="UTC",
        label="Display timezone (IANA name, e.g. Europe/London)",
        validator=_validate_timezone,
    ),
    SettingSpec(
        domain=SettingDomain.display,
        key="date_format",
        value_type=SettingValueType.string,
        default="%Y-%m-%d",
        label="Date display format (strftime)",
        validator=_validate_strftime,
    ),
    SettingSpec(
        domain=SettingDomain.display,
        key="datetime_format",
        value_type=SettingValueType.string,
        default="%Y-%m-%d %H:%M",
        label="Date+time display format (strftime)",
        validator=_validate_strftime,
    ),
```

- [ ] **Step 6: Migration** — `alembic upgrade` must widen the DB constraint. Create `alembic/versions/20260718_0006_display_setting_domain.py` (chain `down_revision` to the current head — check `poetry run alembic heads`). Copy the constraint expression style from the migration that created `domain_settings` (grep `ck_domain_settings_domain` under `alembic/versions/`), then:

```python
def upgrade() -> None:
    op.drop_constraint("ck_domain_settings_domain", "domain_settings", type_="check")
    op.create_check_constraint(
        "ck_domain_settings_domain",
        "domain_settings",
        "domain IN ('auth', 'audit', 'branding', 'custom_fields', 'display')",
    )


def downgrade() -> None:
    # Rows in the removed domain would violate the restored constraint.
    op.execute("DELETE FROM domain_settings WHERE domain = 'display'")
    op.drop_constraint("ck_domain_settings_domain", "domain_settings", type_="check")
    op.create_check_constraint(
        "ck_domain_settings_domain",
        "domain_settings",
        "domain IN ('auth', 'audit', 'branding', 'custom_fields')",
    )
```

(Adjust the literal domain lists to match the ACTUAL existing constraint text found by the grep — do not guess; the upgrade list = existing + `'display'`.)

- [ ] **Step 7: Temporary orphan allowance is NOT allowed** — the no-orphan-settings test will now be RED (three keys, no reader until Task 2). Do NOT touch `_ALLOWED_ORPHAN_SETTINGS` (shrink-only rule). Instead Task 1 and Task 2 land as ONE PR branch with per-task commits; run the orphan test at Task 1 end EXPECTING failure and note it in the commit message as resolved-by-next-commit. Every OTHER suite must be green:
  `poetry run pytest tests/unit tests/architecture -q --deselect tests/architecture/test_no_orphan_settings.py::test_every_registered_setting_has_a_reader` (use the real test node id from the file).

- [ ] **Step 8: Verify GREEN** — `poetry run pytest tests/unit/test_display_settings.py -q` → all pass. `make check` clean.

- [ ] **Step 9: Commit**

```bash
git add app/core/settings_models.py app/core/settings_resolver.py \
  app/features/settings/spec.py alembic/versions/20260718_0006_display_setting_domain.py \
  tests/unit/test_display_settings.py
git commit -m "feat(settings): display domain — timezone/date formats with write-time validators

Orphan-settings gate intentionally RED this commit; the readers land in the
next commit (app/core/display.py)."
```

### Task 2: Per-request display resolution + Jinja filters + governance

**Files:**
- Create: `app/core/display.py`
- Modify: `app/core/templating.py`, `app/core/web_deps.py` (require_web_auth), `templates/admin/rbac/grants.html:106`, `templates/admin/rbac/_audit_table.html:26`
- Test: `tests/unit/test_display_settings.py` (extend), `tests/architecture/test_web_conventions.py`

**Interfaces:**
- Consumes: Task 1's three spec keys (quoted literals `"timezone"`, `"date_format"`, `"datetime_format"` — these satisfy the orphan gate), `SettingDomain.display`.
- Produces: `get_request_display(request, db) -> DisplaySettings`; Jinja filters `local_datetime`/`local_date`; hard rule "templates render `*_at` timestamps only through `local_*` filters" (governance test).

- [ ] **Step 1: Failing filter + page tests** — extend `tests/unit/test_display_settings.py`:

```python
from datetime import UTC, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.core.display import DisplaySettings
from app.core.templating import templates


def _fake_request_ctx(display: DisplaySettings | None) -> dict:
    state = SimpleNamespace()
    if display is not None:
        state.display = display
    return {"request": SimpleNamespace(state=state)}


class TestLocalFilters:
    def test_local_datetime_converts_to_request_timezone_and_format(self) -> None:
        tmpl = templates.env.from_string("{{ value | local_datetime }}")
        display = DisplaySettings(
            timezone=ZoneInfo("America/New_York"),
            date_format="%Y-%m-%d",
            datetime_format="%d %b %Y %H:%M",
        )
        value = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
        out = tmpl.render(value=value, **_fake_request_ctx(display))
        assert out == "18 Jul 2026 08:00"  # UTC-4 in July (EDT)

    def test_local_datetime_treats_naive_as_utc(self) -> None:
        # SQLite (unit DB) returns naive datetimes; models store UTC.
        tmpl = templates.env.from_string("{{ value | local_datetime }}")
        display = DisplaySettings(
            timezone=ZoneInfo("Europe/London"),
            date_format="%Y-%m-%d",
            datetime_format="%H:%M",
        )
        out = tmpl.render(
            value=datetime(2026, 7, 18, 12, 0), **_fake_request_ctx(display)
        )
        assert out == "13:00"  # BST = UTC+1

    def test_local_datetime_falls_back_when_state_not_warmed(self) -> None:
        # Error pages / unauthenticated renders never resolved display —
        # the filter must not raise and must use spec defaults (UTC).
        tmpl = templates.env.from_string("{{ value | local_datetime }}")
        out = tmpl.render(
            value=datetime(2026, 7, 18, 12, 0, tzinfo=UTC),
            **_fake_request_ctx(None),
        )
        assert out == "2026-07-18 12:00"

    def test_local_datetime_of_none_is_empty(self) -> None:
        tmpl = templates.env.from_string("{{ value | local_datetime }}")
        assert tmpl.render(value=None, **_fake_request_ctx(None)) == ""

    def test_local_date_uses_date_format(self) -> None:
        tmpl = templates.env.from_string("{{ value | local_date }}")
        display = DisplaySettings(
            timezone=ZoneInfo("UTC"),
            date_format="%d/%m/%Y",
            datetime_format="%Y-%m-%d %H:%M",
        )
        out = tmpl.render(
            value=datetime(2026, 7, 18, 23, 30, tzinfo=UTC),
            **_fake_request_ctx(display),
        )
        assert out == "18/07/2026"
```

Plus an end-to-end page test in the same file, following `tests/unit/test_settings_web.py`'s app-building pattern EXACTLY (bare `FastAPI()` + `register_error_handlers` + auth web router + rbac web router + `_inject_tenant` middleware + `dependency_overrides[get_db]` + `registered_admin` + `_login`):

```python
class TestGrantsPageUsesTenantDisplay:
    def test_grants_page_renders_created_at_in_tenant_timezone(
        self, client, db, tenant_row, registered_admin
    ) -> None:
        upsert_by_key(
            db, SettingDomain.display, "timezone", "America/New_York",
            tenant_id=tenant_row.id,
        )
        upsert_by_key(
            db, SettingDomain.display, "datetime_format", "%d %b %Y %H:%M",
            tenant_id=tenant_row.id,
        )
        db.commit()
        token = _login(client, registered_admin.email)
        resp = client.get(
            "/admin/rbac/grants", cookies={"access_token": token}
        )
        assert resp.status_code == 200
        # Test files may query directly (thin-wrapper rule scopes to app/).
        grant_created = db.scalars(
            select(PartyRole.created_at).order_by(PartyRole.created_at.desc())
        ).first()
        assert grant_created is not None
        expected = (
            grant_created.replace(tzinfo=UTC)
            .astimezone(ZoneInfo("America/New_York"))
            .strftime("%d %b %Y %H:%M")
        )
        assert expected in resp.text
```

(`from sqlalchemy import select`, `from app.core.models import PartyRole`. Implementer: resolve the exact grants route path and fixture/helper names from `tests/unit/test_settings_web.py` and `app/features/rbac/web.py` — reuse, don't reinvent. If registration doesn't create a PartyRole grant row, create one via `rbac` service in the test's arrange step, same as the existing rbac web tests do.)

- [ ] **Step 2: Governance test RED** — add to `tests/architecture/test_web_conventions.py`:

```python
_JINJA_EXPR = re.compile(r"{{(.*?)}}", re.S)


def test_timestamp_renders_go_through_local_filters() -> None:
    """A raw `{{ x.created_at }}` bypasses tenant display settings.

    Any Jinja expression rendering a `*_at` attribute must apply
    `local_datetime`/`local_date` ("local_date" substring covers both).
    """
    offenders: list[str] = []
    for path in _template_files():  # reuse this module's existing template iterator
        for match in _JINJA_EXPR.finditer(path.read_text()):
            expr = match.group(1)
            if re.search(r"\b\w+_at\b", expr) and "local_date" not in expr:
                offenders.append(f"{path}: {{{{{expr.strip()}}}}}")
    assert not offenders, (
        "Raw timestamp renders (add `| local_datetime` or `| local_date`): "
        + "; ".join(offenders)
    )
```

(Reuse the file-iteration helper/glob the module already has — match its scope, `templates/**` wide is fine here since fragments count too.) Run: RED with exactly the two known offenders (`grants.html`, `_audit_table.html`) — if it catches more, fix those too; if fewer, the regex is wrong.

- [ ] **Step 3: `app/core/display.py`**

```python
"""Per-request tenant display settings (timezone + date/datetime formats).

Mirrors app.core.branding: resolved at most once per request, memoized on
`request.state.display`, warmed by `require_web_auth`. Templates consume it
ONLY via the `local_datetime`/`local_date` Jinja filters registered in
app.core.templating (governance:
tests/architecture/test_web_conventions.py::test_timestamp_renders_go_through_local_filters).

The JSON API is deliberately untouched: responses remain ISO-8601 UTC.
Display formatting is a web-portal presentation concern; API consumers do
their own localization.
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Request
from sqlalchemy.orm import Session

from app.core.settings_models import SettingDomain
from app.core.settings_resolver import get_spec, resolve_value

_UTC = ZoneInfo("UTC")


@dataclass(frozen=True)
class DisplaySettings:
    timezone: ZoneInfo
    date_format: str
    datetime_format: str


def default_display() -> DisplaySettings:
    """Spec-default display — used when there is no tenant or no warmed state."""
    return DisplaySettings(
        timezone=_UTC,
        date_format=str(get_spec(SettingDomain.display, "date_format").default),
        datetime_format=str(
            get_spec(SettingDomain.display, "datetime_format").default
        ),
    )


def load_display(db: Session, tenant_id: UUID) -> DisplaySettings:
    tz_name = resolve_value(
        db, SettingDomain.display, "timezone", tenant_id=tenant_id
    )
    try:
        tz = ZoneInfo(str(tz_name))
    except (ZoneInfoNotFoundError, ValueError):
        # resolve_value already degrades validator-failing rows to the spec
        # default, so this is belt-and-braces (e.g. tzdata missing at
        # runtime): a render must never 500 over a timezone lookup.
        tz = _UTC
    return DisplaySettings(
        timezone=tz,
        date_format=str(
            resolve_value(
                db, SettingDomain.display, "date_format", tenant_id=tenant_id
            )
        ),
        datetime_format=str(
            resolve_value(
                db, SettingDomain.display, "datetime_format", tenant_id=tenant_id
            )
        ),
    )


def get_request_display(request: Request, db: Session) -> DisplaySettings:
    cached = getattr(request.state, "display", None)
    if cached is not None:
        return cached
    tenant = getattr(request.state, "tenant", None)
    display = (
        load_display(db, tenant.id) if tenant is not None else default_display()
    )
    request.state.display = display
    return display
```

- [ ] **Step 4: Filters in `app/core/templating.py`** — after the `templates = Jinja2Templates(...)` singleton:

```python
from datetime import UTC, date, datetime

from jinja2 import pass_context

from app.core.display import DisplaySettings, default_display


def _context_display(context: Any) -> DisplaySettings:
    request = context.get("request")
    display = (
        getattr(request.state, "display", None) if request is not None else None
    )
    # Fail-safe for renders that never warmed request.state.display (error
    # pages, unauthenticated pages): spec defaults, never an exception.
    return display if display is not None else default_display()


@pass_context
def local_datetime(context: Any, value: datetime | None) -> str:
    if value is None:
        return ""
    display = _context_display(context)
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)  # SQLite returns naive UTC
    return value.astimezone(display.timezone).strftime(display.datetime_format)


@pass_context
def local_date(context: Any, value: datetime | date | None) -> str:
    if value is None:
        return ""
    display = _context_display(context)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        value = value.astimezone(display.timezone).date()
    return value.strftime(display.date_format)


templates.env.filters["local_datetime"] = local_datetime
templates.env.filters["local_date"] = local_date
```

Update the templating module docstring line that says no custom filters were ported — it now has exactly these two, and says why (`display settings consumption point`). Check import direction: `templating` → `display` → `settings_resolver`; none of these import `templating` back (no cycle).

- [ ] **Step 5: Warm the seam** — in `app/core/web_deps.py::require_web_auth`, immediately after the existing `get_request_branding(request, db)` call, add `get_request_display(request, db)` (import at module top). Login/error pages are NOT warmed on purpose — they render no timestamps; the filter fallback covers any future accident (pinned by `test_local_datetime_falls_back_when_state_not_warmed`).

- [ ] **Step 6: Fix the two templates**
  - `templates/admin/rbac/grants.html:106`: `{{ grant.created_at }}` → `{{ grant.created_at | local_datetime }}`
  - `templates/admin/rbac/_audit_table.html:26`: `{{ event.created_at }}` → `{{ event.created_at | local_datetime }}`

- [ ] **Step 7: Verify all GREEN** — `poetry run pytest tests/unit tests/architecture -q` (orphan gate now passes: `"timezone"`/`"date_format"`/`"datetime_format"` are quoted in `app/core/display.py`, which is inside the reader corpus). Governance test GREEN. `make check` clean.

- [ ] **Step 8: Integration + smoke** — `TEST_DB_PORT=5437 make test-db-up && TEST_DB_PORT=5437 make test-integration && make test-db-down` (migration applies; 43 canaries green — settings isolation canary already covers `domain_settings` rows generically, so tenant isolation of display rows is inherited). Then `make docker-build` health smoke as in prior tasks.

- [ ] **Step 9: Commit**

```bash
git add app/core/display.py app/core/templating.py app/core/web_deps.py \
  templates/admin/rbac/grants.html templates/admin/rbac/_audit_table.html \
  tests/unit/test_display_settings.py tests/architecture/test_web_conventions.py
git commit -m "feat(web): tenant display settings consumed at render — local_datetime/local_date filters"
```

### Task 3: Docs + v0.7.0 + final review + PR merge

**Files:**
- Modify: `CLAUDE.md`, `docs/ARCHITECTURE.md`, `CHANGELOG.md`, `docs/superpowers/phase2-backlog.md`, `VERSION` + `pyproject.toml` (via `make bump-version part=minor`)

**Interfaces:** Consumes everything above; produces the merged PR.

- [ ] **Step 1: CLAUDE.md** — add to hard rules: templates render `*_at` timestamps ONLY via `local_datetime`/`local_date` (name the governance test); add one paragraph to the web-portal section: display settings domain, the `request.state.display` seam (mirror of branding), API-stays-UTC boundary. Keep it tight — CLAUDE.md links, ARCHITECTURE explains.
- [ ] **Step 2: ARCHITECTURE.md** — "Display settings" subsection: the three specs, write-loud/read-degrade validator split, per-request seam, filter fallback invariant, migration note (check constraint widened; downgrade deletes display rows — documented data-loss-on-downgrade). Ownership table row: display formats — owner `settings (display domain)`, consumers = Jinja filters only.
- [ ] **Step 3: CHANGELOG 0.7.0** — Added: display settings domain (tenant timezone + date/datetime formats, auto-appearing in /admin/settings); `SettingSpec.validator`; `local_datetime`/`local_date` filters + governance test. No breaking changes.
- [ ] **Step 4: Backlog** — add: timezone `<select>`/picker UI in the generic settings editor (the `allowed`-set → dropdown gap disclosed in 2b recon, now also relevant to free-text timezone entry); number/currency locale formatting (no render sites exist yet — YAGNI until one does, note Babel as the likely dep). Strike anything the display work delivered.
- [ ] **Step 5: Bump + gates** — `make bump-version part=minor` (→ 0.7.0); full `make check` + unit + architecture + integration; commit `docs: display settings + v0.7.0`.
- [ ] **Step 6: Final review** — whole-branch review (range main..display-settings) scoped to: settings-SoT held (no parallel format constants), read-path can never 500 a render, governance test actually sensitive (would it catch a new raw `{{ x.updated_at }}`? — verify by temporary mutation), migration list matches the real constraint. Fix findings, re-verify.
- [ ] **Step 7: PR → green → merge** — push; `gh pr create` (title `Display settings: tenant timezone + date/datetime formats (v0.7.0)`); `gh workflow run ci.yml --repo michaelayoade/dotmac_starter_mt --ref display-settings`; monitor to green; `gh pr merge --merge --delete-branch`; sync local main; ledger + knowledge-server memory update.

## Completion criteria

- A tenant admin can change timezone/date/datetime formats in `/admin/settings` (auto-rendered) and every portal timestamp follows immediately; invalid values are rejected loudly at write; a corrupt stored value can never 500 a render (degrades to defaults).
- Governance prevents future raw timestamp renders; no-orphan gate green; API responses byte-identical to before.
- Suites green (unit+arch, 43 integration, docker smoke); PR merged; v0.7.0.
