"""Shadow parity: connector rows == incumbent Sub writer rows.

Fixtures were produced by Sub's own row builder
(`radius_population._projection_rows_for_item`) over work items translated
by Sub's `network_projection_plan.translate_work_item`, then frozen here so
drift in either renderer fails this suite without a cross-repo import.
Regenerate from Sub when the incumbent renderer changes; never hand-edit
the incumbent rows.
"""

from __future__ import annotations

import json
from pathlib import Path

from dotmac_connector_freeradius.render import RenderConfig, render_projection

_CASES = json.loads(
    (Path(__file__).with_name("shadow_incumbent_cases.json")).read_text()
)


def _norm(rows: object) -> list[tuple[tuple[str, str], ...]]:
    return sorted(
        tuple(sorted((str(k), str(v)) for k, v in dict(row).items()))
        for row in rows  # type: ignore[union-attr]
    )


def test_rendered_rows_match_the_incumbent_writer() -> None:
    cfg = _CASES["config"]
    groups = _CASES["access_groups"]
    config = RenderConfig(
        password_attribute=cfg["password_attribute"],
        password_op=cfg["password_op"],
        default_reply_op=cfg["default_reply_op"],
        use_group=cfg["use_group"],
        group_priority=cfg["group_priority"],
        active_group=groups.get("active"),
        captive_group=groups.get("captive"),
        suspended_group=groups.get("suspended"),
        access_group_priority=10,
    )
    assert len(_CASES["cases"]) == 4
    for case in _CASES["cases"]:
        rendered = render_projection(
            case["step_input"], config=config, cleartext_secret=case["cleartext"]
        )
        assert _norm(rendered.check_rows) == _norm(case["incumbent"]["check"]), case[
            "username"
        ]
        assert _norm(rendered.reply_rows) == _norm(case["incumbent"]["reply"]), case[
            "username"
        ]
        assert _norm(rendered.group_rows) == _norm(case["incumbent"]["group"]), case[
            "username"
        ]
