"""`scripts/check_new_ip_literals.py` — new public IP literals are refused.

Only synthetic and documentation addresses appear here. Any "global" value is
built in code so no real address is committed to this public repository.
"""

from __future__ import annotations

import ipaddress
import subprocess

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


def test_python_slices_are_not_flagged() -> None:
    for text in ["a[::2]", "a[1::2]", "rows[2::4]", "s[10::]", "x[:: -1]"]:
        assert public_addresses(text) == [], text


def test_an_added_line_starting_with_plus_plus_is_content_not_a_header() -> None:
    diff = _diff("a.txt", "+++ x.png", f"+{GLOBAL_V4}")
    # The first added line is "++ x.png"; it must not switch the path or skip.
    assert [(f.path, f.line) for f in scan_diff(diff)] == [("a.txt", 2)]
    leak = _diff("a.txt", f"+++ {GLOBAL_V4}")
    found = scan_diff(leak)
    assert [f.path for f in found] == ["a.txt"]


def test_url_and_zero_prefix_forms_are_judged_by_the_host() -> None:
    assert public_addresses(f"http://{GLOBAL_V4}/123/x") == ["8.x.x.x"]
    assert public_addresses(f"http://{GLOBAL_V4}:8080/x") == ["8.x.x.x"]
    assert public_addresses(f"{GLOBAL_V4}/1") == ["8.x.x.x"]
    assert public_addresses(f"{GLOBAL_V4}/0") == ["8.x.x.x"]
    assert public_addresses("[" + GLOBAL_V6 + "]:443") == ["2606:x"]


def test_mapped_forms_are_unwrapped_explicitly() -> None:
    value = int(ipaddress.ip_address(GLOBAL_V4))
    hexed = f"{value >> 16:x}:{value & 0xFFFF:x}"
    assert len(public_addresses(f"::ffff:{hexed}")) == 1
    assert len(public_addresses(f"::ffff:{GLOBAL_V4}")) == 1
    assert public_addresses("::ffff:10.1.2.3") == []


def test_zero_padded_ipv4_is_judged_after_normalising() -> None:
    padded = ".".join(f"{int(part):03d}" for part in GLOBAL_V4.split("."))
    assert padded != GLOBAL_V4
    assert public_addresses(f"host {padded}") == ["8.x.x.x"]
    assert public_addresses("host 010.001.002.003") == []


def test_seven_groups_with_trailing_double_colon_is_caught() -> None:
    groups = ["2606", "4700", "4700", "1111", "1001", "2", "3"]
    assert public_addresses(":".join(groups) + "::") == ["2606:x"]


def test_svg_is_skipped() -> None:
    assert scan_diff(_diff("static/icon.svg", f"+{GLOBAL_V4}")) == []


def test_empty_base_exits_2(tmp_path) -> None:
    diff = tmp_path / "d.diff"
    diff.write_text("")
    assert main(["--base", "", "--diff-file", str(diff)]) == 2
    assert main(["--base", "   "]) == 2


def _git(repo, *args: str) -> str:
    return subprocess.run(  # noqa: S603
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],  # noqa: S607
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_git_paths_text_rename_and_unresolvable_base(
    tmp_path, monkeypatch, capsys
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "legacy.txt").write_text(f"old {GLOBAL_V4}\nline two\nline three\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    monkeypatch.chdir(repo)

    # A pure rename of existing content is not new content.
    _git(repo, "mv", "legacy.txt", "moved.txt")
    _git(repo, "commit", "-q", "-m", "rename")
    assert main(["--base", base]) == 0

    # A file marked binary (and one with a NUL byte) is still read as text.
    (repo / ".gitattributes").write_text("*.cfg binary\n")
    (repo / "x.cfg").write_text(f"host = {GLOBAL_V4}\n")
    (repo / "nul.txt").write_bytes(b"\x00 " + GLOBAL_V4.encode() + b"\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "add")
    assert main(["--base", base]) == 1
    out = capsys.readouterr().out
    assert "x.cfg:1" in out and "nul.txt:1" in out
    assert GLOBAL_V4 not in out

    assert main(["--base", "0" * 40]) == 2
    assert main(["--base=--output=x"]) == 2
