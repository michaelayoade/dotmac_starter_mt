"""`scripts/check_new_ip_literals.py` — new public IP literals are refused.

Only synthetic and documentation addresses appear here. Any "global" value is
built in code so no real address is committed to this public repository.
"""

from __future__ import annotations

import ipaddress

from scripts.check_new_ip_literals import main, public_addresses, scan_diff

# A globally routable value, assembled at runtime rather than written down.
GLOBAL_V4 = ".".join(["8", "8", "4", "4"])
GLOBAL_V6 = ":".join(["2606", "4700", "4700", "", "1001"])


def _diff(path: str, *lines: str, start: int = 1) -> str:
    body = "\n".join(lines)
    return (
        f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
        f"@@ -0,0 +{start},{len(lines)} @@\n{body}\n"
    )


def test_the_synthetic_globals_really_are_global() -> None:
    assert ipaddress.ip_address(GLOBAL_V4).is_global
    assert ipaddress.ip_address(GLOBAL_V6).is_global


def test_an_added_public_ipv4_is_flagged_with_file_line_and_mask() -> None:
    findings = scan_diff(_diff("docs/x.md", "+ok", f"+host {GLOBAL_V4} here", start=7))
    assert [(f.path, f.line, f.masked) for f in findings] == [
        ("docs/x.md", 8, "8.x.x.x")
    ]


def test_an_added_public_ipv6_is_flagged() -> None:
    findings = scan_diff(_diff("a.py", f"+x = '{GLOBAL_V6}'"))
    assert [f.masked for f in findings] == ["2606:x"]


def test_a_public_cidr_is_flagged() -> None:
    assert public_addresses(f"allow {GLOBAL_V4}/24") == ["8.x.x.x"]


def test_non_public_and_documentation_addresses_pass() -> None:
    passing = [
        "10.1.2.3",
        "172.16.0.9",
        "192.168.1.1",
        "127.0.0.1",
        "169.254.1.1",
        "100.64.0.1",  # CGNAT
        "0.0.0.0",  # noqa: S104
        "192.0.2.10",
        "198.51.100.10",
        "203.0.113.10",
        "100.64.0.0/10",
        "::1",
        "fe80::1",
        "fd00::1",
        "2001:db8::1",
        "::",
    ]
    for value in passing:
        assert public_addresses(f"addr {value} end") == [], value


def test_version_strings_and_lookalikes_do_not_match() -> None:
    for text in [
        "version 1.2.3",
        "pkg==2.31.0",
        "v1.2.3.4",
        "build 1.2.3.4.5",
        "time 10:00:00",
        "sha256:abcd",
        "999.1.1.1",
        "01.02.03.04",
    ]:
        assert public_addresses(text) == [], text


def test_the_marker_opts_a_line_out_but_needs_a_reason() -> None:
    marked = f"x {GLOBAL_V4}  # ip-literal-ok: public resolver in a vendor example"
    assert public_addresses(marked) == []
    assert public_addresses(f"x {GLOBAL_V4}  # ip-literal-ok:") == ["8.x.x.x"]


def test_removed_context_and_deleted_file_lines_are_ignored() -> None:
    diff = (
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
        f"@@ -3,2 +3,1 @@\n-old {GLOBAL_V4}\n context {GLOBAL_V4}\n+new 10.0.0.1\n"
        "diff --git a/gone.txt b/gone.txt\n--- a/gone.txt\n+++ /dev/null\n"
        f"@@ -1 +0,0 @@\n-{GLOBAL_V4}\n"
    )
    assert scan_diff(diff) == []


def test_lockfiles_and_binaries_are_skipped() -> None:
    assert scan_diff(_diff("poetry.lock", f"+{GLOBAL_V4}")) == []
    assert scan_diff(_diff("static/logo.png", f"+{GLOBAL_V4}")) == []
    assert scan_diff(_diff("pkg/package-lock.json", f"+{GLOBAL_V4}")) == []


def test_line_numbers_follow_added_lines_across_hunks() -> None:
    diff = (
        "diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n"
        f"@@ -1,0 +5,2 @@\n+one\n+{GLOBAL_V4}\n"
        f"@@ -20 +30 @@\n+{GLOBAL_V4}\n"
    )
    assert [f.line for f in scan_diff(diff)] == [6, 30]


def test_cli_exit_codes_and_masked_output(tmp_path, capsys) -> None:
    bad = tmp_path / "bad.diff"
    bad.write_text(_diff("a.txt", f"+{GLOBAL_V4}"))
    assert main(["--base", "unused", "--diff-file", str(bad)]) == 1
    out = capsys.readouterr()
    assert "a.txt:1" in out.out and "8.x.x.x" in out.out
    assert GLOBAL_V4 not in out.out + out.err

    good = tmp_path / "good.diff"
    good.write_text(_diff("a.txt", "+192.0.2.1"))
    assert main(["--base", "unused", "--diff-file", str(good)]) == 0
    assert main(["--base", "unused", "--diff-file", str(tmp_path / "missing")]) == 2
