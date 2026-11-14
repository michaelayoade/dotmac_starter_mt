"""Refuse NEW public IP address literals added by a pull request.

This repository is public. Policy (Michael, 2026-11-14): an IP address is not a
secret, so existing content is left alone; the fix is going forward only. This
check reads `git diff --unified=0 <base>...HEAD`, inspects ADDED lines only, and
fails when one adds a globally routable IPv4 or IPv6 address (or CIDR network).

Allowed without a marker: private, loopback, link-local, CGNAT and unspecified
addresses, plus the documentation ranges (RFC 5737, RFC 3849). Anything the
`ipaddress` module reports as not `is_global` is allowed; the documentation
ranges are also allowed explicitly so the policy does not depend on the Python
version's table.

A line may opt out with an inline marker carrying a reason:

    ip-literal-ok: <reason>

Lockfiles and binary files are skipped. Output names the file, the line number
and a MASKED address (first octet only), never the full value.

Use role names in code and docs, documentation ranges in tests, and real
addresses only at deploy time (see CONTRIBUTING.md).

Exit status: 0 clean, 1 findings, 2 usage or git error.
"""

from __future__ import annotations

import argparse
import ipaddress
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import PurePosixPath

MARKER = re.compile(r"ip-literal-ok:\s*\S")

_V4 = re.compile(r"(?<![\w.])(\d{1,3}(?:\.\d{1,3}){3})(?:/(\d{1,3}))?(?!\w|\.\d)")
_V6 = re.compile(
    r"(?<![\w:])([0-9A-Fa-f]{0,4}(?::[0-9A-Fa-f]{0,4}){2,7})(?:/(\d{1,3}))?(?![\w:])"
)

_DOCUMENTATION = tuple(
    ipaddress.ip_network(n)
    for n in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24", "2001:db8::/32")
)

_LOCKFILE_NAMES = frozenset(
    {
        "poetry.lock",
        "package-lock.json",
        "npm-shrinkwrap.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "uv.lock",
        "pipfile.lock",
        "cargo.lock",
        "composer.lock",
        "gemfile.lock",
        "go.sum",
        "podfile.lock",
    }
)
_BINARY_SUFFIXES = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".ico",
        ".pdf",
        ".zip",
        ".gz",
        ".tgz",
        ".bz2",
        ".xz",
        ".woff",
        ".woff2",
        ".ttf",
        ".otf",
        ".eot",
        ".so",
        ".dylib",
        ".dll",
        ".exe",
        ".whl",
        ".pyc",
        ".jar",
        ".mp4",
        ".mov",
        ".sqlite",
        ".db",
    }
)

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    masked: str


def is_skipped_path(path: str) -> bool:
    pure = PurePosixPath(path)
    name = pure.name.lower()
    return name in _LOCKFILE_NAMES or pure.suffix.lower() in _BINARY_SUFFIXES


def _is_flagged(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None:
        address = mapped
    if any(address in net for net in _DOCUMENTATION if net.version == address.version):
        return False
    return bool(address.is_global)


def _mask(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str:
    if address.version == 4:
        return f"{str(address).split('.', 1)[0]}.x.x.x"
    return f"{address.exploded.split(':', 1)[0].lstrip('0') or '0'}:x"


def public_addresses(line: str) -> list[str]:
    """Masked form of every public address or network on one line of text."""
    if MARKER.search(line):
        return []
    masked: list[str] = []
    for pattern in (_V4, _V6):
        for match in pattern.finditer(line):
            text, prefix = match.group(1), match.group(2)
            try:
                address = ipaddress.ip_address(text)
            except ValueError:
                continue
            if prefix is not None:
                try:
                    network = ipaddress.ip_network(f"{text}/{prefix}", strict=False)
                except ValueError:
                    pass
                else:
                    address = network.network_address
            if _is_flagged(address):
                masked.append(_mask(address))
    return masked


def scan_diff(diff_text: str) -> list[Finding]:
    """Findings for ADDED lines of a `--unified=0` diff. Removed and context
    lines, deleted files, lockfiles and binaries are ignored."""
    findings: list[Finding] = []
    path: str | None = None
    skip = True
    new_line = 0
    for raw in diff_text.splitlines():
        if raw.startswith("diff --git "):
            path, skip, new_line = None, True, 0
        elif raw.startswith("+++ "):
            target = raw[4:]
            if target == "/dev/null":
                path, skip = None, True
            else:
                path = target[2:] if target.startswith("b/") else target
                skip = is_skipped_path(path)
        elif raw.startswith("--- "):
            continue
        elif raw.startswith("@@"):
            hunk = _HUNK.match(raw)
            new_line = int(hunk.group(1)) if hunk else 0
        elif raw.startswith("+"):
            if path is not None and not skip:
                for masked in public_addresses(raw[1:]):
                    findings.append(Finding(path, new_line, masked))
            new_line += 1
    return findings


def _git_diff(base: str) -> str:
    result = subprocess.run(
        [
            "git",
            "-c",
            "core.quotePath=false",
            "diff",
            "--unified=0",
            "--no-color",
            "--no-ext-diff",
            f"{base}...HEAD",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "git diff failed (is the base fetched? shallow checkouts have no "
            f"merge base): {result.stderr.strip()[:300]}"
        )
    return result.stdout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base", required=True, help="base commit SHA to diff against")
    parser.add_argument(
        "--diff-file",
        help="read a unified diff from this file instead of running git (testing)",
    )
    args = parser.parse_args(argv)
    try:
        if args.diff_file:
            with open(args.diff_file, encoding="utf-8", errors="replace") as handle:
                diff = handle.read()
        else:
            diff = _git_diff(args.base)
    except (RuntimeError, OSError) as exc:
        print(f"check_new_ip_literals: {exc}", file=sys.stderr)
        return 2
    findings = scan_diff(diff)
    for item in findings:
        print(f"{item.path}:{item.line}: new public IP address {item.masked}")
    if findings:
        print(
            "\nThis repository is public. Use a role name in code and docs, a "
            "documentation range (RFC 5737 / RFC 3849) in tests, and real "
            "addresses only at deploy time. A deliberate exception takes an "
            "inline marker on the line: ip-literal-ok: <reason>",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
