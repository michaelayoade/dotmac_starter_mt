#!/usr/bin/env python3
"""Installed tagged-source floor evidence. Registry wheel identity is separate.

Run by the hosted floor lane with python -I. Unit doubles exercise classification
and evidence checks only, never installed registration or published compatibility.
"""

import argparse
import csv
import hashlib
import importlib
import importlib.metadata
import importlib.util
import json
import re
import sys
import sysconfig
import zipfile
from email.parser import BytesParser
from pathlib import Path

LEARNING_TABLES = frozenset(
    {
        "courses",
        "activities",
        "submissions",
        "scores",
        "course_completions",
        "certificates",
        "learning_events",
        "chapters",
        "question_banks",
        "questions",
        "chapter_reads",
        "subtopic_reads",
        "chapter_bookmarks",
        "chapter_notes",
        "cohorts",
        "enrollments",
        "tracks",
        "track_courses",
        "cohort_tracks",
        "course_offerings",
        "course_prerequisites",
        "activity_attempts",
        "attempt_grants",
        "offering_activities",
        "class_sessions",
        "announcements",
        "success_queue_entries",
        "question_bank_versions",
    }
)
REGISTRY_CODES = {
    "dotmac_learning": ("learning", "learning", "lr", "learning"),
    "dotmac_fulfillment": ("fulfillment", "fulfillment", "fu", "fulfillment"),
    "dotmac_template_studio": ("template_studio", "tstudio", "ts", "template_studio"),
}


def parse_entry(entry):
    fields = entry.split(":")
    if not 2 <= len(fields) <= 4:
        raise ValueError(
            "want package_dir:import_name[:consumer,..."
            "[:manifest|registry|tenant-audit]]"
        )
    package, name = fields[:2]
    extra = fields[2] if len(fields) >= 3 else ""
    mode = fields[3] if len(fields) == 4 else "manifest"
    dotted = r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*"
    if (
        not re.fullmatch(r"packages/[A-Za-z0-9_-]+", package)
        or not re.fullmatch(dotted, name)
        or mode not in {"manifest", "registry", "tenant-audit"}
        or (
            extra
            and any(
                not re.fullmatch(dotted, part) or not part.startswith(name + ".")
                for part in extra.split(",")
            )
        )
        or (mode == "registry" and name != "dotmac_learning")
        or (
            mode == "tenant-audit"
            and name not in {"dotmac_fulfillment", "dotmac_template_studio"}
        )
    ):
        raise ValueError("malformed entry or unknown probe mode/package")
    return package, name, extra, mode


def check_origin(origin, venv, checkout):
    path = Path(origin).resolve()
    venv, checkout = Path(venv).resolve(), Path(checkout).resolve()
    roots = [
        Path(
            sysconfig.get_path(key, vars={"base": str(venv), "platbase": str(venv)})
        ).resolve()
        for key in ("purelib", "platlib")
    ]
    if (
        not path.is_relative_to(venv)
        or path.is_relative_to(checkout)
        or not any(
            root.is_relative_to(venv) and path.is_relative_to(root) for root in roots
        )
    ):
        raise ValueError(f"invalid installed origin: {path}")
    return str(path)


def check_learning_pure_origin(origin, distribution, venv, checkout):
    """Require the pure sibling to be owned by the installed Learning RECORD."""
    checked = check_origin(origin, venv, checkout)
    check_version(distribution.metadata["Name"], "dotmac-learning", "pure owner")
    record = distribution.read_text("RECORD")
    if record is None:
        raise ValueError("missing Learning installed RECORD")
    matches = [
        row[0]
        for row in csv.reader(record.splitlines())
        if row
        and row[0].startswith("dotmac_learning_bank_rules/")
        and ".." not in Path(row[0]).parts
        and Path(distribution.locate_file(row[0])).resolve() == Path(checked)
    ]
    if len(matches) != 1:
        raise ValueError(
            "pure origin absent or duplicated in Learning installed RECORD"
        )
    return checked, matches[0]


def check_version(actual, expected, label):
    if actual != expected:
        raise ValueError(f"{label} version mismatch: {actual!r} != {expected!r}")


def audit_refusal(exc):
    return (
        type(exc) is ImportError
        and re.fullmatch(
            r"cannot import name 'TENANT_AUDIT_LOG_V1' "
            r"from 'dotmac_kernel.prerequisites'(?: \([^\n]+\))?",
            str(exc),
        )
        is not None
    )


def check_registration(module, registry, namespace_type):
    namespaces = registry.namespaces()
    if (
        not isinstance(namespaces, namespace_type)
        or registry.get(module.code) is not module
    ):
        raise ValueError(
            "registry did not retain genuine manifest/namespace registration"
        )
    expected = (
        module.code,
        f"mod_{module.short_code}",
        module.migration_prefix,
        module.migration_branch,
    )
    owner = namespaces.get(module.code)
    if (
        owner is None
        or (owner.owner, owner.db_schema, owner.prefix, owner.branch_label) != expected
    ):
        raise ValueError("registered allocation differs from manifest")
    tables, platform = frozenset(module.tables), frozenset(module.platform_tables)
    if not tables or len(tables) != len(module.tables):
        raise ValueError("empty or duplicate declared tables")
    if module.code == "learning" and (tables != LEARNING_TABLES or platform):
        raise ValueError("Learning must declare exactly its current 28 tenant tables")
    if namespaces.declared_tables(expected[1]) != tables | platform:
        raise ValueError("registered table catalog differs from manifest")
    if namespaces.declared_platform_tables(expected[1]) != platform:
        raise ValueError("registered platform catalog differs from manifest")
    return {
        "owner": expected[0],
        "schema": expected[1],
        "prefix": expected[2],
        "branch": expected[3],
        "tables": sorted(tables),
        "platform_tables": sorted(platform),
    }


def wheel_metadata(wheel):
    with zipfile.ZipFile(wheel) as archive:
        names = [
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        ]
        if len(names) != 1:
            raise ValueError(
                "wheel must contain exactly one distribution metadata record"
            )
        return BytesParser().parsebytes(archive.read(names[0]))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_evidence(result, expected, mode, name):
    required = {
        "status",
        "reason",
        "kernel_version",
        "module_version",
        "origins",
        "module_wheel_sha256",
        "kernel_wheel_sha256",
        "kernel_tag_commit",
        "mode",
    }
    if (
        not required <= result.keys()
        or result["status"] != expected
        or result["mode"] != mode
    ):
        raise ValueError("incomplete evidence or wrong outcome")
    if (
        not isinstance(result["origins"], dict)
        or not {"dotmac_kernel", name} <= result["origins"].keys()
    ):
        raise ValueError("missing package provenance")
    if name == "dotmac_learning":
        pure_origin = result["origins"].get("dotmac_learning_bank_rules")
        if not isinstance(pure_origin, str) or not pure_origin:
            raise ValueError("missing pure package provenance")
        if result.get("pure_provenance") != {
            "distribution": "dotmac-learning",
            "record": "dotmac_learning_bank_rules/__init__.py",
        }:
            raise ValueError("missing pure installed distribution provenance")
    if not re.fullmatch(r"[0-9a-f]{40}", result["kernel_tag_commit"]):
        raise ValueError("missing peeled tag commit")
    for key in ("module_wheel_sha256", "kernel_wheel_sha256"):
        if not re.fullmatch(r"[0-9a-f]{64}", result[key]):
            raise ValueError("missing wheel identity")
    if mode in {"registry", "tenant-audit"}:
        if "allocation" not in result:
            raise ValueError("missing independently inspected shipped ledger")
        if expected == "REFUSED":
            refusal = result.get("refusal", {})
            if (
                result["reason"] != "missing TENANT_AUDIT_LOG_V1"
                or refusal.get("type") != "ImportError"
                or not audit_refusal(ImportError(refusal.get("message", "")))
            ):
                raise ValueError("unrelated registry refusal")
        if mode == "registry" and expected == "OK" and "registration" not in result:
            raise ValueError("missing actual registration")
    elif expected == "REFUSED" and name == "dotmac_billing":
        if not result["reason"].startswith(
            "ImportError: cannot import name 'conflict_savepoint_requires_rollback' "
            "from 'dotmac_kernel.transactions'"
        ):
            raise ValueError("unrelated Billing refusal")


def installed_probe(args):
    result = {
        "status": "BROKEN",
        "reason": "probe incomplete",
        "mode": args.mode,
        "kernel_version": args.kernel_version,
        "module_version": args.module_version,
        "module_wheel_sha256": digest(args.module_wheel),
        "kernel_wheel_sha256": digest(args.kernel_wheel),
        "kernel_tag_commit": args.commit,
        "origins": {},
    }
    venv, checkout = Path(sys.prefix), Path(args.checkout)
    kernel_metadata = wheel_metadata(args.kernel_wheel)
    check_version(kernel_metadata["Name"], "dotmac-kernel", "kernel wheel name")
    check_version(kernel_metadata["Version"], args.kernel_version, "kernel wheel")
    module_metadata = wheel_metadata(args.module_wheel)
    check_version(module_metadata["Name"], args.distribution, "module wheel name")
    check_version(module_metadata["Version"], args.module_version, "module wheel")
    for distribution, expected in (
        ("dotmac-kernel", args.kernel_version),
        (args.distribution, args.module_version),
    ):
        dist = importlib.metadata.distribution(distribution)
        check_version(dist.version, expected, distribution)
        check_origin(dist.locate_file(""), venv, checkout)
    kernel = importlib.import_module("dotmac_kernel")
    check_version(kernel.__version__, args.kernel_version, "kernel runtime")
    target_spec = importlib.util.find_spec(args.name)
    if target_spec is None or target_spec.origin is None:
        raise ValueError("installed target package absent")
    result["origins"][args.name] = check_origin(target_spec.origin, venv, checkout)
    for loaded in (kernel,):
        result["origins"][loaded.__name__] = check_origin(
            loaded.__file__, venv, checkout
        )
    if args.name == "dotmac_learning":
        check_version(args.distribution, "dotmac-learning", "Learning distribution")
        learning_distribution = dist
        # This sibling remains importable even when the Learning root refuses
        # the previous Kernel's missing tenant-audit API.
        pure = importlib.import_module("dotmac_learning_bank_rules")
        pure_origin, pure_record = check_learning_pure_origin(
            pure.__file__, learning_distribution, venv, checkout
        )
        if pure_record != "dotmac_learning_bank_rules/__init__.py":
            raise ValueError("unexpected pure package entry point")
        result["origins"]["dotmac_learning_bank_rules"] = pure_origin
        result["pure_provenance"] = {
            "distribution": "dotmac-learning",
            "record": pure_record,
        }
    if args.mode in {"registry", "tenant-audit"}:
        ns = importlib.import_module("dotmac_kernel.namespaces")
        result["origins"][ns.__name__] = check_origin(ns.__file__, venv, checkout)
        code, short, prefix, branch = REGISTRY_CODES[args.name]
        matches = [row for row in ns.MIGRATION_OWNER_LEDGER if row.owner == code]
        if len(matches) > 1:
            raise ValueError("duplicate shipped allocation")
        result["allocation"] = {
            "present": bool(matches),
            "rows": [
                {
                    "owner": row.owner,
                    "schema": row.db_schema,
                    "prefix": row.prefix,
                    "branch": row.branch_label,
                }
                for row in matches
            ],
        }
    refusals = (TypeError, ImportError, AttributeError)
    try:
        prereqs = importlib.import_module("dotmac_kernel.prerequisites")
    except ImportError:
        pass
    else:
        refusals += (prereqs.PrerequisiteError,)
    try:
        package = importlib.import_module(args.name)
        if hasattr(package, "__version__"):
            check_version(package.__version__, args.module_version, "module runtime")
        manifest = importlib.import_module(f"{args.name}.manifest")
        module = manifest.module
        if args.mode in {"registry", "tenant-audit"}:
            if prereqs.TENANT_AUDIT_LOG_V1.name not in module.requires:
                raise ValueError("manifest omits required tenant audit effect")
        check_version(module.version, args.module_version, "manifest")
        result["origins"][manifest.__name__] = check_origin(
            manifest.__file__, venv, checkout
        )
        for consumer in filter(None, args.extra.split(",")):
            if importlib.util.find_spec(consumer) is None:
                raise ValueError(f"declared consumer absent: {consumer}")
            imported = importlib.import_module(consumer)
            result["origins"][consumer] = check_origin(
                imported.__file__, venv, checkout
            )
        if args.mode == "registry":
            if (
                module.code,
                module.short_code,
                module.migration_prefix,
                module.migration_branch,
            ) != (code, short, prefix, branch):
                raise ValueError("unexpected installed module allocation")
            from dotmac_kernel.modules import ModuleRegistry

            registry = ModuleRegistry((module,))
            result["registration"] = check_registration(
                module, registry, ns.NamespaceRegistry
            )
            if not matches:
                raise ValueError("positive registry has no shipped allocation")
    except refusals as exc:
        result["refusal"] = {"type": type(exc).__name__, "message": str(exc)}
        result["reason"] = f"{type(exc).__name__}: {exc}"
        if args.mode == "manifest":
            result["status"] = "REFUSED"
        elif audit_refusal(exc):
            result.update(status="REFUSED", reason="missing TENANT_AUDIT_LOG_V1")
    else:
        result.update(status="OK", reason="installed manifest loaded")
    # Also verify every successfully imported module under the package roots.
    roots = ("dotmac_kernel.", args.name + ".")
    if args.name == "dotmac_learning":
        roots += ("dotmac_learning_bank_rules.",)
    for name, loaded in tuple(sys.modules.items()):
        if name.startswith(roots) and getattr(loaded, "__file__", None):
            if args.name == "dotmac_learning" and name.startswith(
                "dotmac_learning_bank_rules."
            ):
                result["origins"][name], _ = check_learning_pure_origin(
                    loaded.__file__, learning_distribution, venv, checkout
                )
            else:
                result["origins"][name] = check_origin(loaded.__file__, venv, checkout)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    entry = sub.add_parser("entry")
    entry.add_argument("entry")
    info = sub.add_parser("wheel-info")
    info.add_argument("wheel")
    deps = sub.add_parser("dependencies")
    deps.add_argument("wheel")
    deps.add_argument("kernel_version")
    deps.add_argument("venv")
    run = sub.add_parser("run")
    for option in (
        "name",
        "extra",
        "mode",
        "kernel-version",
        "module-version",
        "distribution",
        "module-wheel",
        "kernel-wheel",
        "commit",
        "checkout",
    ):
        run.add_argument("--" + option, required=True)
    check = sub.add_parser("validate")
    for option in (
        "file",
        "expected",
        "mode",
        "name",
        "kernel-version",
        "module-version",
        "module-wheel",
        "kernel-wheel",
        "commit",
    ):
        check.add_argument("--" + option, required=True)
    args = parser.parse_args()
    if args.command == "entry":
        print("\t".join(value or "-" for value in parse_entry(args.entry)))
    elif args.command == "wheel-info":
        metadata = wheel_metadata(args.wheel)
        print(metadata["Name"], metadata["Version"])
    elif args.command == "dependencies":
        requirements = wheel_metadata(args.wheel).get_all("Requires-Dist", [])
        runtime = []
        for requirement in requirements:
            match = re.match(r"[A-Za-z0-9_.-]+", requirement)
            if not match:
                raise ValueError("malformed wheel dependency")
            if re.sub(r"[-_.]+", "-", match.group()).lower() != "dotmac-kernel":
                runtime.append(requirement)
        Path(args.venv, "module-runtime.txt").write_text("\n".join(runtime) + "\n")
        Path(args.venv, "kernel-constraint.txt").write_text(
            f"dotmac-kernel=={args.kernel_version}\n"
        )
    elif args.command == "validate":
        result = json.loads(Path(args.file).read_text())
        validate_evidence(result, args.expected, args.mode, args.name)
        for key, expected in (
            ("kernel_version", args.kernel_version),
            ("module_version", args.module_version),
            ("module_wheel_sha256", digest(args.module_wheel)),
            ("kernel_wheel_sha256", digest(args.kernel_wheel)),
            ("kernel_tag_commit", args.commit),
        ):
            check_version(result[key], expected, key)
    else:
        if args.mode not in {"manifest", "registry", "tenant-audit"}:
            raise ValueError("unknown mode")
        parse_entry(f"packages/probe:{args.name}:{args.extra}:{args.mode}")
        result = installed_probe(args)
        print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
