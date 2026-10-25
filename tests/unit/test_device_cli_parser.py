"""Huawei `display ont info <port> all` parsing: tolerant, never a silent empty."""

from __future__ import annotations

from dotmac_device_cli import Reason, Unreadable
from dotmac_device_cli.core.classifier import classify_table
from dotmac_device_cli.core.dialect import dialect_for
from dotmac_device_cli.core.results import Evidence
from dotmac_device_cli.dialects.huawei import normalize

D = dialect_for("huawei")
assert D is not None
EV = Evidence("p", "d", "t", "", 0)
SN = "4857544300000001"


def test_spaced_fsp_is_compacted() -> None:
    assert normalize(f"0/ 2/1    0  {SN}  active") == f"0/2/1    0  {SN}  active"
    assert normalize(f"0/ 2/ 1  7  {SN}") == f"0/2/1  7  {SN}"


def test_ansi_and_pager_lines_are_removed_and_rows_survive_on_the_same_line() -> None:
    raw = (
        f"  0/2/1    0  {SN}  active  online  normal  match  no\r\n"
        "  ---- More ( Press 'Q' to break ) ----\x1b[37D     \x1b[37D"
        f"  0/2/1    1  4857544300000002  active  offline  normal  match  no\r\n"
        "\x1b[2K  In port 0/2/1, the total of ONTs are: 2, online: 1\r\n"
    )
    table = D.parse_ont_table(raw)
    assert [r.ref.ont_id for r in table.rows] == [0, 1]
    assert table.declared_total == 2 and table.data_row_hints == 2


def test_protect_side_and_description_columns() -> None:
    desc = "CUSTOMER_03_zone_Zone_1_authd_20240101"
    row = f"  0/2/1    3  {SN}  active  online  normal  match  no  {desc}"
    (parsed,) = D.parse_ont_table(row).rows
    assert parsed.description == "CUSTOMER_03_zone_Zone_1_authd_20240101"
    assert parsed.serial == SN and parsed.ref.native == "0/2/1 3"


def test_empty_text_is_unreadable_never_an_empty_list() -> None:
    out = classify_table(D.parse_ont_table(""), EV)
    assert isinstance(out, Unreadable) and out.reason is Reason.UNRECOGNIZED_RESPONSE


def test_only_the_devices_own_words_make_a_port_empty() -> None:
    explicit = classify_table(
        D.parse_ont_table("the total of ONTs are: 0, online: 0"), EV
    )
    assert not isinstance(explicit, Unreadable) and explicit.value.explicitly_empty
    header_only = classify_table(D.parse_ont_table("  F/S/P   ONT   SN"), EV)
    assert (
        isinstance(header_only, Unreadable) and header_only.reason is Reason.TRUNCATED
    )
