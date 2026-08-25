"""The composed migration gate (ADR-0006 D1, item 6).

Loads **every selected Alembic version location** of a composition and rejects
it before an image can be built when the composed lineages are not coherent:

===========================  ==========================================
Rejection                    Why it is fatal
===========================  ==========================================
duplicate revision id        two owners' `alembic_version` rows collide
duplicate migration prefix   independently released lineages can mint
                             the same id later
duplicate branch label       an `alembic_version` row stops being
                             attributable to one owner
duplicate schema claim       two modules own one namespace
duplicate table ownership    two migrations create the same qualified
                             table — the F0 collision class, which fails
                             QUIETLY (an `add_column` mutates someone
                             else's table)
===========================  ==========================================

plus the lineage and qualification rules D1 states alongside them: one lineage
root per owner carrying that owner's branch label, `down_revision` never
crossing owners (cross-lineage ordering is `depends_on`), revision ids inside
`alembic_version.version_num`'s VARCHAR(32), and module DDL that never depends
on `search_path`.

## Static, not executed

Every check is an **AST scan** of the revision files. Nothing is imported and
no database is touched, so the gate runs in the same cheap CI step as lint —
which is the point: it must fail before an image is built, not during a deploy.
The repo's other governance tests (`tests/architecture/*`) use the same
technique for the same reason.

## Attribution is by branch label, not by a hand-maintained map

Each version location is attributed to an owner through its **lineage root**:
the one revision with `down_revision = None`, whose `branch_labels` name the
owner. That is deliberately the same mechanism D1 item 5 relies on to explain
`alembic_version` rows, so there is no second, drift-prone location→owner table
to keep in sync with `alembic.ini`. Adding a module's migrations to a
deployment therefore means exactly two edits: its version location in the
Alembic config, and its ledger row in `dotmac_kernel.namespaces`.

## Two grandfathered lineages

`kernel` (`0001_initial_tenant_schema` …) and `assembly` (`a001_adopt_cfd` …)
predate D1. Their ids are already recorded in live `alembic_version` rows, so
they keep their original format via `MigrationOwner.legacy_revision_pattern`
and are exempt from the `<prefix>_<sequence>_<slug>` and `schema=` rules —
their tables legitimately live in the `public` compatibility namespace. Every
other owner gets the strict rules.
"""

from __future__ import annotations

import ast
import configparser
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from dotmac_kernel.modules import AnyManifest
from dotmac_kernel.namespaces import (
    HOST_SCHEMA,
    MAX_REVISION_ID_LENGTH,
    MigrationOwner,
    NamespaceError,
    NamespaceRegistry,
    module_schema,
    qualified,
)

# `op.*` calls that create or alter a table and therefore MUST name the schema
# explicitly in a module lineage. Anything here without `schema=` resolves
# through `search_path`, which is connection state — exactly what D1 forbids.
SCHEMA_QUALIFIED_OPS: frozenset[str] = frozenset(
    {
        "create_table",
        "drop_table",
        "rename_table",
        "add_column",
        "alter_column",
        "drop_column",
        "create_index",
        "drop_index",
        "create_unique_constraint",
        "create_check_constraint",
        "create_foreign_key",
        "drop_constraint",
        "create_primary_key",
        "bulk_insert",
    }
)


@dataclass(frozen=True, slots=True)
class RevisionRecord:
    """One migration file's lineage identity, read statically."""

    path: Path
    location: Path
    revision: str
    down_revision: tuple[str, ...]
    branch_labels: tuple[str, ...]
    depends_on: tuple[str, ...]
    # Qualified tables this revision CREATES (`op.create_table`), as
    # `schema.table`; an unqualified create is recorded against `public`, which
    # is what makes an accidental unqualified module create visible as a
    # `public` table-ownership claim rather than as nothing at all.
    creates_tables: tuple[str, ...] = ()
    # `op.<name>` calls that omit `schema=`, and raw `op.execute("…")` string
    # literals — the two ways a migration can end up `search_path`-dependent.
    unqualified_ops: tuple[str, ...] = ()
    # Schema-mutating operations with the statically resolved TARGET schema.
    # A qualified write into another module is still a cross-owner write.
    targeted_ops: tuple[tuple[str, str], ...] = ()
    raw_sql: tuple[str, ...] = ()

    @property
    def is_root(self) -> bool:
        return not self.down_revision


@dataclass(frozen=True, slots=True)
class GateReport:
    """The composed gate's verdict. `ok` is the only thing CI needs; the rest
    is what a human needs to fix it."""

    revisions: tuple[RevisionRecord, ...] = ()
    violations: tuple[str, ...] = ()
    attribution: Mapping[str, Mapping[str, str | None]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.violations

    def render(self) -> str:
        if self.ok:
            owners = sorted({str(a["owner"]) for a in self.attribution.values()})
            return (
                f"migration gate PASS — {len(self.revisions)} revision(s) across "
                f"{len(owners)} owner(s): {', '.join(owners)}"
            )
        lines = [f"migration gate FAIL — {len(self.violations)} violation(s):"]
        lines.extend(f"  - {v}" for v in self.violations)
        return "\n".join(lines)


# ── Reading a version location ──────────────────────────────────────────────


_UNRESOLVED = object()


def _static_value(
    node: ast.AST,
    expressions: Mapping[str, ast.AST] | None = None,
    resolving: frozenset[str] = frozenset(),
) -> object:
    """Resolve the small, side-effect-free expression set migrations use.

    In addition to literals, understand module constants, f-strings and the
    D1 `module_schema`/`qualified` helpers. Anything else stays unresolved and
    therefore fails closed when it is used as a schema or raw-SQL argument.
    """
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError):
        pass

    expressions = expressions or {}
    if isinstance(node, ast.Name):
        if node.id in resolving or node.id not in expressions:
            return _UNRESOLVED
        return _static_value(
            expressions[node.id], expressions, resolving | frozenset({node.id})
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _static_value(node.left, expressions, resolving)
        right = _static_value(node.right, expressions, resolving)
        if isinstance(left, str) and isinstance(right, str):
            return left + right
        return _UNRESOLVED
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
                continue
            if isinstance(value, ast.FormattedValue) and value.format_spec is None:
                resolved = _static_value(value.value, expressions, resolving)
                if resolved is not _UNRESOLVED:
                    parts.append(str(resolved))
                    continue
            return _UNRESOLVED
        return "".join(parts)
    if isinstance(node, ast.Call):
        func = node.func
        name = (
            func.id
            if isinstance(func, ast.Name)
            else func.attr
            if isinstance(func, ast.Attribute)
            else None
        )
        args = [_static_value(arg, expressions, resolving) for arg in node.args]
        if name == "module_schema" and len(args) == 1 and isinstance(args[0], str):
            try:
                return module_schema(args[0])
            except NamespaceError:
                return _UNRESOLVED
        if (
            name == "qualified"
            and len(args) == 2
            and all(isinstance(arg, str) for arg in args)
        ):
            return qualified(str(args[0]), str(args[1]))
        # SQLAlchemy's `text("...")` wrapper does not make static SQL dynamic.
        if name == "text" and len(args) == 1 and isinstance(args[0], str):
            return args[0]
    return _UNRESOLVED


def _literal(node: ast.AST, expressions: Mapping[str, ast.AST] | None = None) -> object:
    value = _static_value(node, expressions)
    return None if value is _UNRESOLVED else value


def _as_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list | tuple | set | frozenset):
        return tuple(str(v) for v in value if v is not None)
    return ()


def _inline_fk_references(
    create_table: ast.Call, expressions: Mapping[str, ast.AST]
) -> tuple[str | None, ...]:
    """Resolve SQLAlchemy FK referents embedded in `op.create_table`."""
    references: list[str | None] = []
    for node in ast.walk(create_table):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (
            func.id
            if isinstance(func, ast.Name)
            else func.attr
            if isinstance(func, ast.Attribute)
            else None
        )
        target: object = _UNRESOLVED
        if name == "ForeignKey" and node.args:
            target = _static_value(node.args[0], expressions)
        elif name == "ForeignKeyConstraint" and len(node.args) >= 2:
            target = _static_value(node.args[1], expressions)
        else:
            continue
        if isinstance(target, str):
            references.append(target)
        elif isinstance(target, list | tuple | set | frozenset):
            references.extend(
                str(item) if isinstance(item, str) else None for item in target
            )
        else:
            references.append(None)
    return tuple(references)


def _scan_ddl(
    tree: ast.Module, expressions: Mapping[str, ast.AST]
) -> tuple[list[str], list[str], list[tuple[str, str]], list[str]]:
    """Created tables, unqualified ops, targeted ops and raw SQL.

    Starts at `upgrade()` and follows local helper calls recursively. Merely
    moving DDL into `_create_tables()` must not turn the gate blind. Downgrade
    remains outside the reachable call graph unless upgrade explicitly calls
    it, so legitimate rollback-only re-creation is not an ownership claim.
    """
    functions = {
        node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)
    }
    upgrade = functions.get("upgrade")
    if upgrade is None:
        return [], [], [], []
    creates: list[str] = []
    unqualified: list[str] = []
    targeted: list[tuple[str, str]] = []
    raw_sql: list[str] = []

    class Calls(ast.NodeVisitor):
        def __init__(self) -> None:
            self.nodes: list[ast.Call] = []

        def visit_Call(self, node: ast.Call) -> None:
            self.nodes.append(node)
            self.generic_visit(node)

        # A nested function is not executed merely because its definition is.
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            return

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            return

    pending = [upgrade]
    visited: set[str] = set()
    while pending:
        function = pending.pop()
        if function.name in visited:
            continue
        visited.add(function.name)
        visitor = Calls()
        for statement in function.body:
            visitor.visit(statement)
        for node in visitor.nodes:
            if isinstance(node.func, ast.Name) and node.func.id in functions:
                pending.append(functions[node.func.id])
            func = node.func
            if not isinstance(func, ast.Attribute):
                continue
            value = func.value
            if not (isinstance(value, ast.Name) and value.id == "op"):
                continue
            name = func.attr
            if name == "execute":
                if node.args:
                    literal = _static_value(node.args[0], expressions)
                    if isinstance(literal, str):
                        raw_sql.append(literal)
                    else:
                        raw_sql.append("<uninspectable dynamic SQL>")
                continue
            if name not in SCHEMA_QUALIFIED_OPS:
                continue
            keywords = {
                keyword.arg: _static_value(keyword.value, expressions)
                for keyword in node.keywords
                if keyword.arg is not None
            }
            if name == "create_foreign_key":
                source_schema = keywords.get("source_schema", _UNRESOLVED)
                referent_schema = keywords.get("referent_schema", _UNRESOLVED)
                if not isinstance(source_schema, str):
                    unqualified.append("create_foreign_key(source_schema)")
                else:
                    targeted.append((name, source_schema))
                if not isinstance(referent_schema, str):
                    unqualified.append("create_foreign_key(referent_schema)")
                schema = source_schema
            elif name == "bulk_insert":
                # `bulk_insert` has no schema kwarg; its first argument is
                # normally an inline `sa.table(..., schema=...)` object.
                schema = _UNRESOLVED
                if node.args and isinstance(node.args[0], ast.Call):
                    table_keywords = {
                        keyword.arg: _static_value(keyword.value, expressions)
                        for keyword in node.args[0].keywords
                        if keyword.arg is not None
                    }
                    schema = table_keywords.get("schema", _UNRESOLVED)
                if not isinstance(schema, str):
                    unqualified.append(name)
                else:
                    targeted.append((name, schema))
            else:
                schema = keywords.get("schema", _UNRESOLVED)
                if not isinstance(schema, str):
                    unqualified.append(name)
                elif name != "create_table":
                    targeted.append((name, schema))
            if name == "create_table" and node.args:
                table = _literal(node.args[0], expressions)
                if isinstance(table, str):
                    creates.append(
                        qualified(
                            schema if isinstance(schema, str) else HOST_SCHEMA,
                            table,
                        )
                    )
                for reference in _inline_fk_references(node, expressions):
                    if reference is None or len(reference.split(".")) < 3:
                        unqualified.append("ForeignKeyConstraint(referent)")
    return creates, unqualified, targeted, raw_sql


def scan_revision_file(path: Path, location: Path) -> RevisionRecord | None:
    """Read one revision file's identity statically. `None` when the file
    declares no `revision` (an `__init__.py`, a helper module)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    expressions: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    expressions[target.id] = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.value is not None:
                expressions[node.target.id] = node.value
    assigned: dict[str, object] = {}
    for name, expression in expressions.items():
        assigned[name] = _literal(expression, expressions)
    revision = assigned.get("revision")
    if not isinstance(revision, str) or not revision:
        return None
    creates, unqualified, targeted, raw_sql = _scan_ddl(tree, expressions)
    return RevisionRecord(
        path=path,
        location=location,
        revision=revision,
        down_revision=_as_tuple(assigned.get("down_revision")),
        branch_labels=_as_tuple(assigned.get("branch_labels")),
        depends_on=_as_tuple(assigned.get("depends_on")),
        creates_tables=tuple(creates),
        unqualified_ops=tuple(unqualified),
        targeted_ops=tuple(targeted),
        raw_sql=tuple(raw_sql),
    )


def scan_location(location: Path) -> tuple[RevisionRecord, ...]:
    """Every revision in one version location, sorted by file name."""
    records = []
    for path in sorted(location.glob("*.py")):
        if path.name.startswith("__"):
            continue
        record = scan_revision_file(path, location)
        if record is not None:
            records.append(record)
    return tuple(records)


def version_locations_from_ini(ini_path: Path) -> tuple[Path, ...]:
    """The `version_locations` an Alembic config SELECTS, resolved to paths.

    Reads the config the deployment actually uses rather than a second list, so
    a location added to `alembic.ini` is automatically a location the gate must
    attribute to an owner (and fails the gate if it cannot).
    """
    parser = configparser.ConfigParser()
    parser.read_string(ini_path.read_text(encoding="utf-8"))
    here = str(ini_path.resolve().parent)
    raw = parser.get("alembic", "version_locations", fallback="", raw=True)
    raw = raw.replace("%(here)s", here)
    separator = parser.get("alembic", "path_separator", fallback="space", raw=True)
    parts = raw.split(":" if separator == "os" else None)
    if separator not in ("space", "os"):
        parts = raw.split(separator)
    resolved = []
    for part in parts:
        candidate = part.strip()
        if candidate:
            resolved.append((ini_path.resolve().parent / candidate).resolve())
    return tuple(resolved)


# ── The gate ────────────────────────────────────────────────────────────────


def run_gate(
    manifests: Iterable[AnyManifest],
    locations: Sequence[Path],
    *,
    registry: NamespaceRegistry | None = None,
) -> GateReport:
    """Compose `locations` under the namespaces `manifests` declare and report
    every violation found.

    Never raises for a composition problem — a gate that raises on the first
    fault makes an operator fix one thing per CI run. It raises only for an
    unreadable input (a missing directory), which is a caller bug.
    """
    violations: list[str] = []
    if registry is None:
        try:
            registry = NamespaceRegistry.from_manifests(manifests)
        except NamespaceError as exc:
            # Duplicate schema claims, duplicate prefixes, duplicate branch
            # labels, duplicate table ownership and ledger drift all surface
            # here: the namespace registry IS the declaration-side gate.
            return GateReport(violations=(f"namespace composition: {exc}",))

    records: list[RevisionRecord] = []
    owner_of_location: dict[Path, MigrationOwner] = {}
    for location in locations:
        if not location.is_dir():
            violations.append(
                f"version location {location} does not exist — the composed "
                "Alembic config selects a directory that is not shipped"
            )
            continue
        found = scan_location(location)
        if not found:
            violations.append(f"version location {location} contains no revisions")
            continue
        records.extend(found)
        owner = _attribute_location(location, found, registry, violations)
        if owner is not None:
            owner_of_location[location] = owner

    _check_duplicate_revisions(records, violations)
    _check_revision_ids(records, owner_of_location, violations)
    _check_lineage_edges(records, owner_of_location, violations)
    creator = _check_table_ownership(records, owner_of_location, registry, violations)
    _check_declared_tables_are_built(owner_of_location, registry, creator, violations)
    _check_search_path_independence(records, owner_of_location, violations)
    _check_stateful_modules_have_a_lineage(owner_of_location, registry, violations)

    attribution: dict[str, Mapping[str, str | None]] = {}
    for record in records:
        owner = owner_of_location.get(record.location)
        attribution[record.revision] = {
            "owner": owner.owner if owner else None,
            "branch_label": owner.branch_label if owner else None,
            "db_schema": owner.db_schema if owner else None,
        }
    return GateReport(
        revisions=tuple(records),
        violations=tuple(violations),
        attribution=attribution,
    )


def _attribute_location(
    location: Path,
    records: Sequence[RevisionRecord],
    registry: NamespaceRegistry,
    violations: list[str],
) -> MigrationOwner | None:
    """Resolve a location's owner from its lineage root's branch label."""
    roots = [r for r in records if r.is_root]
    if len(roots) != 1:
        violations.append(
            f"version location {location}: expected exactly ONE lineage root "
            f"(`down_revision = None`), found {len(roots)} "
            f"({', '.join(sorted(r.revision for r in roots)) or 'none'}) — "
            "a migration owner owns one lineage with one base"
        )
        return None
    root = roots[0]
    if len(root.branch_labels) != 1:
        violations.append(
            f"{root.revision}: a lineage root must carry exactly ONE branch "
            f"label (found {list(root.branch_labels)}) — the label is how an "
            "`alembic_version` row is attributed to an owner"
        )
        return None
    label = root.branch_labels[0]
    owner = registry.owner_for_branch(label)
    if owner is None:
        violations.append(
            f"{root.revision}: branch label {label!r} is not registered — "
            "allocate the owner in dotmac_kernel.namespaces."
            "MIGRATION_OWNER_LEDGER before shipping its migrations"
        )
        return None
    return owner


def _check_duplicate_revisions(
    records: Sequence[RevisionRecord], violations: list[str]
) -> None:
    seen: dict[str, RevisionRecord] = {}
    for record in records:
        previous = seen.get(record.revision)
        if previous is not None:
            violations.append(
                f"duplicate revision id {record.revision!r}: {previous.path} and "
                f"{record.path} — one `alembic_version` row, two migrations"
            )
            continue
        seen[record.revision] = record


def _check_revision_ids(
    records: Sequence[RevisionRecord],
    owner_of_location: Mapping[Path, MigrationOwner],
    violations: list[str],
) -> None:
    for record in records:
        if len(record.revision) > MAX_REVISION_ID_LENGTH:
            violations.append(
                f"revision id {record.revision!r} is {len(record.revision)} "
                f"chars; alembic_version.version_num is "
                f"VARCHAR({MAX_REVISION_ID_LENGTH}) — this fails at `alembic "
                "upgrade`, against a real database, not here"
            )
        owner = owner_of_location.get(record.location)
        if owner is None:
            continue
        if not owner.revision_pattern().match(record.revision):
            expected = (
                f"the grandfathered {owner.owner} format "
                f"({owner.legacy_revision_pattern})"
                if owner.is_legacy
                else f"<{owner.prefix}>_<sequence>_<slug>"
            )
            violations.append(
                f"revision id {record.revision!r} ({record.path.name}) does not "
                f"match {expected} for owner {owner.owner!r} — the prefix is "
                "what keeps ids unique across independently released lineages"
            )
        # Also catch a revision carrying a branch label that is not its own.
        for label in record.branch_labels:
            if label != owner.branch_label:
                violations.append(
                    f"{record.revision}: declares branch label {label!r} but "
                    f"lives in the {owner.owner!r} lineage (label "
                    f"{owner.branch_label!r}) — a revision may not relabel "
                    "another owner's branch"
                )


def _check_lineage_edges(
    records: Sequence[RevisionRecord],
    owner_of_location: Mapping[Path, MigrationOwner],
    violations: list[str],
) -> None:
    """`down_revision` stays inside one lineage; cross-lineage ordering is
    `depends_on` (D1, item 4)."""
    owner_of_revision = {
        r.revision: owner_of_location.get(r.location)
        for r in records
        if r.location in owner_of_location
    }
    known = {r.revision for r in records}
    for record in records:
        owner = owner_of_location.get(record.location)
        if owner is None:
            continue
        for parent in record.down_revision:
            parent_owner = owner_of_revision.get(parent)
            if parent not in known:
                violations.append(
                    f"{record.revision}: `down_revision` {parent!r} is not in "
                    "any selected version location"
                )
            elif parent_owner is not None and parent_owner.owner != owner.owner:
                violations.append(
                    f"{record.revision} ({owner.owner}): `down_revision` "
                    f"{parent!r} belongs to lineage {parent_owner.owner!r} — "
                    "cross-lineage ordering uses `depends_on`, never "
                    "`down_revision` (ADR-0006 D1)"
                )
        for dependency in record.depends_on:
            if dependency not in known:
                violations.append(
                    f"{record.revision}: `depends_on` {dependency!r} is not in "
                    "any selected version location"
                )


def _check_table_ownership(
    records: Sequence[RevisionRecord],
    owner_of_location: Mapping[Path, MigrationOwner],
    registry: NamespaceRegistry,
    violations: list[str],
) -> dict[str, tuple[str, Path]]:
    """One qualified table, one creating OWNER — and a module creates only
    inside its own schema, only tables its manifest declares.

    Cross-owner, not cross-revision: one owner's lineage may legitimately drop
    and rebuild its own table over time. Two OWNERS creating one qualified
    table is the F0 collision class, and it is the dangerous one.

    Returns the qualified-table → (owner, defining file) map it built, which is
    the reverse direction's input: `_check_declared_tables_are_built` needs to
    know what the lineages actually create, and rebuilding that map would mean
    two walks that could disagree.
    """
    creator: dict[str, tuple[str, Path]] = {}
    for record in records:
        owner = owner_of_location.get(record.location)
        owner_name = owner.owner if owner else str(record.location)
        for table in record.creates_tables:
            previous = creator.get(table)
            if previous is not None and previous[0] != owner_name:
                violations.append(
                    f"duplicate table ownership: {table} is created by both "
                    f"{previous[0]!r} ({previous[1].name}) and {owner_name!r} "
                    f"({record.path.name}) — same-name/different-shape table "
                    "collisions fail QUIETLY (ADR-0006 D1)"
                )
                continue
            if previous is not None:
                continue
            creator[table] = (owner_name, record.path)
            if owner is None or owner.is_legacy:
                continue
            schema, _, bare = table.partition(".")
            if schema != owner.db_schema:
                violations.append(
                    f"{record.revision} ({owner.owner}): creates {table}, "
                    f"outside its own namespace {owner.db_schema!r} — "
                    f"{HOST_SCHEMA!r} and other modules' schemas are not "
                    "available to an installable module"
                )
                continue
            declared = registry.declared_tables(schema)
            if bare not in declared:
                violations.append(
                    f"{record.revision} ({owner.owner}): creates {table}, "
                    "which its manifest's `tables` does not declare — the "
                    "manifest is the table-ownership declaration"
                )
    return creator


def _check_declared_tables_are_built(
    owner_of_location: Mapping[Path, MigrationOwner],
    registry: NamespaceRegistry,
    creator: Mapping[str, tuple[str, Path]],
    violations: list[str],
) -> None:
    """The other direction of the same declaration: a table the manifest
    declares that no selected revision ever CREATES.

    `_check_table_ownership` walks what migrations create, so it can only ever
    catch created-but-undeclared. Declared-but-never-built was invisible to it
    and surfaced only in the live catalog validator — which deliberately reads
    a migrated PostgreSQL — so an operator met it after `alembic upgrade`
    rather than in `make check`. The module boots, serves every request that
    misses the table, and fails on the first one that hits it.

    Keyed off "created anywhere in this owner's lineage", not "created by the
    head revision": a module may legitimately drop and rebuild its own table
    across releases, and flagging that would push authors toward declaring
    nothing at all.

    A module that ships NO lineage is deliberately not reported here —
    `_check_stateful_modules_have_a_lineage` already names that, and one fault
    should not produce a violation per declared table.
    """
    built: dict[tuple[str, str], set[str]] = {}
    for table, (owner_name, _path) in creator.items():
        schema, _, bare = table.partition(".")
        if schema:
            built.setdefault((owner_name, schema), set()).add(bare)

    attributed = {owner.owner: owner for owner in owner_of_location.values()}
    for owner_name in sorted(attributed):
        owner = attributed[owner_name]
        if owner.is_legacy or owner.db_schema is None:
            continue
        declared = registry.declared_tables(owner.db_schema)
        missing = declared - built.get((owner_name, owner.db_schema), set())
        for bare in sorted(missing):
            violations.append(
                f"module {owner_name!r} declares table "
                f"{qualified(owner.db_schema, bare)} but no selected revision "
                "creates it — the manifest is the table-ownership declaration, "
                "and its models would map to a table that never exists"
            )


def _check_search_path_independence(
    records: Sequence[RevisionRecord],
    owner_of_location: Mapping[Path, MigrationOwner],
    violations: list[str],
) -> None:
    """Module DDL and raw SQL name their schema; only the grandfathered host
    lineages may resolve through `search_path` (D1, item 2)."""
    for record in records:
        owner = owner_of_location.get(record.location)
        if owner is None or owner.is_legacy or owner.db_schema is None:
            continue
        for call in sorted(set(record.unqualified_ops)):
            violations.append(
                f"{record.revision} ({owner.owner}): `op.{call}(...)` omits "
                f"`schema=` — module DDL must be fully qualified to "
                f"{owner.db_schema!r} and never depend on `search_path`"
            )
        for call, schema in sorted(set(record.targeted_ops)):
            if schema == owner.db_schema:
                continue
            violations.append(
                f"{record.revision} ({owner.owner}): `op.{call}(...)` targets "
                f"schema {schema!r} outside its own namespace "
                f"{owner.db_schema!r} — one schema has one writer"
            )
        for statement in record.raw_sql:
            if owner.db_schema not in statement:
                excerpt = " ".join(statement.split())[:60]
                violations.append(
                    f"{record.revision} ({owner.owner}): raw SQL never names "
                    f"{owner.db_schema!r} ({excerpt!r}...) — module SQL must be "
                    "fully qualified and never depend on `search_path`"
                )


def _check_stateful_modules_have_a_lineage(
    owner_of_location: Mapping[Path, MigrationOwner],
    registry: NamespaceRegistry,
    violations: list[str],
) -> None:
    """A module that owns a schema must ship the migrations that build it —
    otherwise its models map to tables no selected location creates."""
    attributed = {owner.owner for owner in owner_of_location.values()}
    for owner in registry.owners():
        if owner.db_schema is None or owner.owner in attributed:
            continue
        violations.append(
            f"module {owner.owner!r} owns schema {owner.db_schema!r} but no "
            "selected version location carries its lineage — its tables would "
            "never be created"
        )


__all__ = [
    "SCHEMA_QUALIFIED_OPS",
    "GateReport",
    "RevisionRecord",
    "run_gate",
    "scan_location",
    "scan_revision_file",
    "version_locations_from_ini",
]
