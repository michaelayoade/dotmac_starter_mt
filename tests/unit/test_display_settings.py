"""Display settings domain: spec registration + validator behavior.

Write path (update_setting/validate_spec_value) rejects loudly; read path
(resolve_value) silently degrades a bad stored row to the spec default —
same split the resolver already applies to allowed/min/max violations.
"""

from __future__ import annotations

import pytest

import app.features.settings.spec  # noqa: F401 — registration side effect
from app.core.exceptions import BadRequestError
from app.core.settings_models import SettingDomain
from app.core.settings_resolver import (
    get_spec,
    resolve_value,
    upsert_by_key,
    validate_spec_value,
)


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
            db,
            SettingDomain.display,
            "timezone",
            "Not/AZone",
            tenant_id=tenant_row.id,
        )
        assert (
            resolve_value(
                db, SettingDomain.display, "timezone", tenant_id=tenant_row.id
            )
            == "UTC"
        )
