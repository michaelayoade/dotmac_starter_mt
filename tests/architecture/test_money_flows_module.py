"""Structural guards for ``dotmac-money-flows`` (ST-1 composition rule).

Every rule is a pure function over source text or a schema description, so each
one is proved twice: once against the real package (must be clean) and once
against a planted violation (must be refused). A guard that has never been seen
to refuse is not evidence.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import re
import sys
import tomllib
from collections.abc import Mapping
from pathlib import Path

import pytest
from dotmac_kernel.namespaces import MIGRATION_OWNER_LEDGER
from dotmac_money_flows import models
from dotmac_money_flows.manifest import INSTANCE_TABLE_ALLOWLIST, module

ROOT = Path(__file__).resolve().parents[2]
PACKAGE_DIR = ROOT / "packages/dotmac-money-flows"
MODULE_ROOT = Path(inspect.getfile(models)).parent
MIGRATIONS = MODULE_ROOT / "migrations/versions"

# --- The named owner list (ST-1) -------------------------------------------
# Full surface: the owner's top-level ``__all__`` may be imported.
FULL_SURFACE_OWNERS = frozenset(
    {
        "dotmac_billing",
        "dotmac_payments",
        "dotmac_approvals",
        "dotmac_durable_timers",
        "dotmac_fulfillment",
        "dotmac_imports",
    }
)
# Contracts only: top-level ``__all__`` names, and none of them may be a function.
CONTRACT_ONLY_OWNERS = frozenset({"dotmac_collections", "dotmac_service_access_policy"})
OWNERS = FULL_SURFACE_OWNERS | CONTRACT_ONLY_OWNERS
SELF = "dotmac_money_flows"
KERNEL = "dotmac_kernel"
# Third-party roots the package itself needs (persistence only).
THIRD_PARTY_ROOTS = frozenset({"sqlalchemy", "alembic"})

# --- Schema vocabulary ------------------------------------------------------
REF_COLUMN = re.compile(r"^[a-z_]+_(id|ref)$")
FORBIDDEN_NAME_PARTS = (
    "amount",
    "balance",
    "total",
    "price",
    "money",
    "lease",
    "claim",
    "attempt",
    "retry",
    "scan",
    "dead_letter",
    "timer",
)
FORBIDDEN_COLUMN_TYPES = frozenset(
    {"Numeric", "Float", "DECIMAL", "NUMERIC", "REAL", "FLOAT", "Double", "DOUBLE"}
)

# --- Literal scan -----------------------------------------------------------
PRODUCT_TOKENS = frozenset(
    {
        "paystack",
        "flutterwave",
        "remita",
        "mono",
        "nira",
        "whatsapp",
        "meta",
        "linkedin",
        "traccar",
        "freeradius",
        "resend",
        "stripe",
        "paypal",
        "sub",
        "erp",
        "academy",
        "crm",
        "omni",
    }
)
IS_PRODUCT = re.compile(r"\bis_[a-z0-9]+\b")

# --- Forbidden and allowlisted owner calls ----------------------------------
FORBIDDEN_DECISION = re.compile(r"^record_[a-z_]*_decision$")
ALWAYS_FORBIDDEN = frozenset({"seal_opening_lane", "issue_document", "IssueDocument"})
# DEFAULT-DENY for the owners that carry money and approval authority. Any name
# imported from one of these owners that is not listed here is refused, so a new
# owner command (``reverse_*``, ``refund_*`` ...) is unreachable until a reviewer
# adds it deliberately. Values are the flows modules (paths relative to the
# package source root) permitted to use the name; ``"*"`` means any flows module.
DEFAULT_DENY_OWNERS = frozenset(
    {
        "dotmac_billing",
        "dotmac_approvals",
        "dotmac_payments",
        "dotmac_service_access_policy",
    }
)
ANY_MODULE = frozenset({"*"})
# D2: the one module that funds a prepaid charge from account credit and issues
# its invoice. The account terms and funding availability reads are shared with
# Settle (see FUNDED_CHARGE_AND_SETTLE below).
FUNDED_CHARGE_ONLY = frozenset({"funded_charge.py"})
# GAP-3: Settle also takes the account lock (get_funding_availability) and reads
# the account terms and the balance to pay a new postpaid invoice. Read-only
# names; the single widening beyond FundedCharge.
FUNDED_CHARGE_AND_SETTLE = frozenset({"funded_charge.py", "settle.py"})
# GAP-4: the stateless X1 reader (``receivables.py``) maps Billing's sealed document
# exposure to Collections' observation. It reads the account terms (the contract type
# at issue, the grace at the due date) and the two exposure types, read-only; Billing
# stays a default-deny owner. Own commit, for architect sign-off.
RECEIVABLES_ONLY = frozenset({"receivables.py"})
FUNDED_CHARGE_SETTLE_AND_RECEIVABLES = FUNDED_CHARGE_AND_SETTLE | RECEIVABLES_ONLY
# GAP-3 / GAP-L1 dating: only the private ``_credit_dating.py`` reads the account
# statement (read-only, one account, no lock) to find the newest credit that
# reached the account; Settle and FundedCharge call it and never read the
# statement themselves.
CREDIT_DATING_ONLY = frozenset({"_credit_dating.py"})
# The §2.6 owner calls F1 will use, plus the contract types they need. PR C may
# narrow this to modules; PR E adds the F3/F5 modules to the restricted names.
OWNER_NAME_ALLOWLIST: Mapping[str, frozenset[str]] = {
    "get_confirmed_cash_v1": ANY_MODULE,
    "GetConfirmedCashV1": ANY_MODULE,
    "get_payment_intent_v1": ANY_MODULE,
    "GetPaymentIntentV1": ANY_MODULE,
    "get_document_exposure": ANY_MODULE,
    "list_open_receivable_documents": ANY_MODULE,
    "OpenReceivableDocumentV1": ANY_MODULE,
    "accept_settlement": ANY_MODULE,
    "AcceptSettlementV1": ANY_MODULE,
    "accept_withholding_discharge": ANY_MODULE,
    "AcceptWithholdingDischarge": ANY_MODULE,
    "SettlementFundingLane": ANY_MODULE,
    "BillingRuleViolation": ANY_MODULE,
    # F2 emits a refs-only access event shaped like these two contract classes.
    # The owner is contracts-only and default-deny: the record_access_input_v2 /
    # decide_access_input functions and the ORM classes stay unreachable.
    "RecordAccessInputV2": FUNDED_CHARGE_ONLY,
    "AccessSignal": FUNDED_CHARGE_ONLY,
    # F2 FundedCharge (D2) reads these and nothing else beyond the restricted
    # commands below. Each is a deliberate, single-module entry.
    "get_effective_account_terms": FUNDED_CHARGE_SETTLE_AND_RECEIVABLES,
    "AccountTermsV1": FUNDED_CHARGE_AND_SETTLE,
    "ReceivableExposureV1": RECEIVABLES_ONLY,
    "ReceivableExposureV2": RECEIVABLES_ONLY,
    "DueDateBasisStatus": RECEIVABLES_ONLY,
    "get_funding_availability": FUNDED_CHARGE_AND_SETTLE,
    "GetFundingAvailabilityV1": FUNDED_CHARGE_AND_SETTLE,
    "get_account_statement": CREDIT_DATING_ONLY,
    "GetAccountStatementV1": CREDIT_DATING_ONLY,
    "get_confirmed_settlement": FUNDED_CHARGE_ONLY,
    "GetConfirmedSettlementV1": FUNDED_CHARGE_ONLY,
    "IssueRatedInvoiceV3": FUNDED_CHARGE_ONLY,
    "IssuedRatedInvoiceV1": FUNDED_CHARGE_ONLY,
    "ObligationSourceRegistry": FUNDED_CHARGE_ONLY,
    "NumberingProvider": FUNDED_CHARGE_ONLY,
    # Owner refusals are caught here to record attention (one module).
    "BillingError": FUNDED_CHARGE_ONLY,
}
# Owner commands only specific flows may touch. Nothing exists yet, so each set
# is empty. PR E adds the F3 module(s) to the first group and the F5 module to
# the reversal group. Reversals are only ever of settlement-acceptance groups;
# the module allowlist is the static enforcement, the group-kind check belongs to
# the F5 behaviour tests.
F3_ONLY_CALLERS: frozenset[str] = frozenset()
F5_REVERSAL_CALLERS: frozenset[str] = frozenset()
# C2a: F1 pays whole invoices from the balance, so Settle may draw available
# credit; D2 adds FundedCharge, which pays its own charge the same way. Nothing
# else may draw credit.
CREDIT_DRAW_CALLERS: frozenset[str] = frozenset({"settle.py", "funded_charge.py"})
# D2: only FundedCharge issues a rated invoice (the prepaid charge it funds).
ISSUE_CALLERS: frozenset[str] = frozenset({"funded_charge.py"})
RESTRICTED_CALLERS: Mapping[str, frozenset[str]] = {
    "apply_available_credit": CREDIT_DRAW_CALLERS,
    "ApplyAvailableCreditCommand": CREDIT_DRAW_CALLERS,
    "issue_rated_invoice": ISSUE_CALLERS,
    "void_document": F3_ONLY_CALLERS,
    "grant_non_cash_credit": F3_ONLY_CALLERS,
    "issue_credit_note": F3_ONLY_CALLERS,
    "reverse_posting_group": F5_REVERSAL_CALLERS,
    "reverse_cash_credit_draw": F5_REVERSAL_CALLERS,
    "reverse_non_cash_credit_application": F5_REVERSAL_CALLERS,
    "reverse_withholding_discharge": F5_REVERSAL_CALLERS,
}
FORBIDDEN_BUILTINS = frozenset(
    {
        "__import__",
        "eval",
        "exec",
        "globals",
        "vars",
        "locals",
        "compile",
        "breakpoint",
    }
)
# Dunder names and attributes are how introspection walks around every other
# guard (``x.__class__.__subclasses__()``, ``__builtins__``, ``__dict__``). Only
# these module-plumbing names are allowed.
ALLOWED_DUNDERS = frozenset({"__all__", "__version__", "__future__"})
# The one structural allowance: SQLAlchemy's declarative mapping needs these two
# assigned in an ORM class body. Assignment only, in ``models.py`` only; never a
# read, never anywhere else.
DECLARATIVE_DUNDERS = frozenset({"__tablename__", "__table_args__"})
DECLARATIVE_MODULE = "models.py"
# The other one: the Alembic lineage locator resolves its own directory from
# ``__file__``. That file, and only that name in it, is exempt.
LOCATOR_MODULE = "migrations/__init__.py"
LOCATOR_DUNDERS = frozenset({"__file__"})
_DUNDER = re.compile(r"^__\w+__$")
# Standard-library modules the package may import. ``importlib`` and ``sys`` are
# deliberately absent: they are how a guard is walked around.
STDLIB_ALLOWED = frozenset(
    {
        "__future__",
        "typing",
        "dataclasses",
        "enum",
        "uuid",
        "collections",
        "datetime",
        "pathlib",
        # GAP-5: a pure stdlib time-zone lookup (reads tzdata only), used by the
        # private period verifier. Own commit, for architect sign-off.
        "zoneinfo",
    }
)


def _source_files(directory: Path = MODULE_ROOT) -> list[Path]:
    return sorted(path for path in directory.rglob("*.py"))


def _relative(path: Path) -> str:
    return path.relative_to(MODULE_ROOT).as_posix()


def _owner_all(owner: str) -> frozenset[str] | None:
    """The owner's top-level ``__all__``, read from source; None if unavailable."""
    init = ROOT / "packages" / owner.replace("_", "-") / "src" / owner / "__init__.py"
    if not init.is_file():
        return None
    for node in ast.parse(init.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets
        ):
            return frozenset(ast.literal_eval(node.value))
    return None


def _is_function_export(owner: str, name: str) -> bool:
    exported = getattr(importlib.import_module(owner), name, None)
    return inspect.isroutine(exported)


# --- Rule 1: imports --------------------------------------------------------
def import_violations(source: str) -> list[str]:
    problems: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root, _, rest = alias.name.partition(".")
                problems.extend(_module_problems(root, rest, None))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue  # relative: inside this package
            if node.module is None:
                continue
            root, _, rest = node.module.partition(".")
            names = [alias.name for alias in node.names]
            if "*" in names:
                problems.append(f"from {node.module} import *: name what you import")
            problems.extend(_module_problems(root, rest, names))
    return problems


def _module_problems(root: str, rest: str, names: list[str] | None) -> list[str]:
    if root in sys.stdlib_module_names or root == "__future__":
        if root in STDLIB_ALLOWED:
            return []
        return [f"imports stdlib module {root}: not on the allowed list"]
    if root == SELF or root in THIRD_PARTY_ROOTS:
        return []
    if root == KERNEL:
        if any(part.startswith("_") for part in rest.split(".") if part):
            return [f"imports a private kernel module: {KERNEL}.{rest}"]
        return []
    if root in OWNERS:
        if rest:
            return [f"imports {root}.{rest}: only the top-level public surface"]
        if names is None:
            return [f"import {root}: import named public names instead"]
        public = _owner_all(root)
        if public is None:
            return [f"{root} exposes no resolvable top-level __all__"]
        problems = []
        for name in names:
            if name not in public:
                problems.append(f"{name} is not in {root}.__all__")
            elif root in CONTRACT_ONLY_OWNERS and _is_function_export(root, name):
                problems.append(f"{root}.{name} is a function; contracts only")
        return problems
    return [f"imports {root}: not the kernel, a named owner or persistence"]


# --- Rule 2: tables and columns --------------------------------------------
def schema_violations(
    tables: Mapping[str, Mapping[str, str]],
    allowlist: frozenset[str] = INSTANCE_TABLE_ALLOWLIST,
) -> list[str]:
    """``tables`` maps table name to ``{column: sqlalchemy type name}``."""
    problems: list[str] = []
    for table, columns in tables.items():
        if table not in allowlist:
            problems.append(f"table {table} is not in INSTANCE_TABLE_ALLOWLIST")
        for name in (table, *columns):
            lowered = name.lower()
            for part in FORBIDDEN_NAME_PARTS:
                if part in lowered:
                    problems.append(f"{table}.{name} contains forbidden word {part!r}")
        for column, type_name in columns.items():
            if column not in models.COMMON_COLUMNS and not REF_COLUMN.match(column):
                problems.append(
                    f"{table}.{column} is neither a common column nor an _id/_ref"
                )
            if type_name in FORBIDDEN_COLUMN_TYPES:
                problems.append(f"{table}.{column} has money-capable type {type_name}")
        missing = set(models.COMMON_COLUMNS) - set(columns)
        if missing:
            problems.append(f"{table} lacks common columns {sorted(missing)}")
    return problems


def _orm_schema() -> dict[str, dict[str, str]]:
    return {
        model.__table__.name: {
            column.name: type(column.type).__name__
            for column in model.__table__.columns
        }
        for model in models.TENANT_MODELS
    }


def _call_name(node: ast.Call) -> str:
    func = node.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def _type_name(node: ast.expr) -> str:
    target = node.func if isinstance(node, ast.Call) else node
    return (
        target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
    )


def migration_schema(source: str) -> dict[str, dict[str, str]]:
    found: dict[str, dict[str, str]] = {}
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Call)
            and _call_name(node) == "create_table"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            columns: dict[str, str] = {}
            for inner in ast.walk(node):
                if (
                    isinstance(inner, ast.Call)
                    and _call_name(inner) == "Column"
                    and len(inner.args) >= 2
                    and isinstance(inner.args[0], ast.Constant)
                ):
                    columns[inner.args[0].value] = _type_name(inner.args[1])
            found[node.args[0].value] = columns
    return found


def migration_added_columns(source: str) -> dict[str, dict[str, str]]:
    """Columns an ``add_column`` call adds: table -> {column: type}."""
    found: dict[str, dict[str, str]] = {}
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Call)
            and _call_name(node) == "add_column"
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Constant)
        ):
            column = node.args[1]
            if (
                isinstance(column, ast.Call)
                and _call_name(column) == "Column"
                and len(column.args) >= 2
                and isinstance(column.args[0], ast.Constant)
            ):
                found.setdefault(node.args[0].value, {})[column.args[0].value] = (
                    _type_name(column.args[1])
                )
    return found


def added_column_violations(added: Mapping[str, Mapping[str, str]]) -> list[str]:
    """An added column obeys the same vocabulary as a created one."""
    problems: list[str] = []
    for table, columns in added.items():
        if table not in INSTANCE_TABLE_ALLOWLIST:
            problems.append(f"table {table} is not in INSTANCE_TABLE_ALLOWLIST")
        for column, type_name in columns.items():
            for part in FORBIDDEN_NAME_PARTS:
                if part in column.lower():
                    problems.append(
                        f"{table}.{column} contains forbidden word {part!r}"
                    )
            if not REF_COLUMN.match(column):
                problems.append(f"{table}.{column} is not an _id/_ref column")
            if type_name in FORBIDDEN_COLUMN_TYPES:
                problems.append(f"{table}.{column} has money-capable type {type_name}")
    return problems


def foreign_key_violations(source: str) -> list[str]:
    """Only the kernel tenants table and this package's own schema may be targets."""
    problems: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value
            if re.fullmatch(r"[a-z_]+\.[a-z_]+(\.[a-z_]+)?", value) and (
                value.startswith("mod_") or value.startswith("public.")
            ):
                allowed = value == "public.tenants.id" or value.startswith(
                    f"{models.SCHEMA}."
                )
                if "." in value and value.count(".") == 2 and not allowed:
                    problems.append(f"foreign key target {value} is another owner's")
    return problems


def migration_security_violations(source: str) -> list[str]:
    text = re.sub(r"\s+", " ", source)
    problems = []
    if "FORCE ROW LEVEL SECURITY" not in text:
        problems.append("row level security is not forced")
    if re.search(r"GRANT [^;]*\bDELETE\b[^;]*TO app_user", text):
        problems.append("the runtime role is granted DELETE")
    if "GRANT ALL" in text.upper():
        problems.append("GRANT ALL is not permitted")
    return problems


# --- Rule 3: literal scan ---------------------------------------------------
def literal_violations(source: str) -> list[str]:
    problems: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom | ast.Import):
            continue
        text = None
        if isinstance(node, ast.Name):
            text = node.id
        elif isinstance(node, ast.Attribute):
            text = node.attr
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
        elif isinstance(node, ast.FunctionDef | ast.ClassDef):
            text = node.name
        if text is None:
            continue
        spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text).lower()
        tokens = re.split(r"[^a-z0-9]+", spaced)
        for token in tokens:
            if token in PRODUCT_TOKENS:
                problems.append(f"product or provider literal {token!r} in {text!r}")
        if IS_PRODUCT.search(spaced) and any(t in PRODUCT_TOKENS for t in tokens):
            problems.append(f"is_<product> branch {text!r}")
    return problems


# --- Rule 4: no transaction control, no wall clock, no dynamic escape -------
_CONTROL_ATTRS = frozenset({"commit", "rollback", "begin_nested", "connect"})
_SESSION_FACTORIES = frozenset(
    {"Session", "sessionmaker", "scoped_session", "create_engine"}
)
# zoneinfo calls that change where the tz database comes from or throw its cache
# away: a time-zone lookup may only read, never reconfigure (GAP-5).
_TZ_ESCAPES = frozenset({"reset_tzpath", "clear_cache", "from_file"})
_TRANSACTION_WORDS = re.compile(r"\b(COMMIT|ROLLBACK|SAVEPOINT|BEGIN)\b", re.IGNORECASE)
_CLOCKS = frozenset(
    {
        "datetime.now",
        "datetime.utcnow",
        "datetime.today",
        "datetime.datetime.now",
        "datetime.datetime.utcnow",
        "datetime.datetime.today",
        "datetime.date.today",
        "time.time",
        "time.time_ns",
        "time.monotonic",
        "time.monotonic_ns",
        "time.perf_counter",
        "time.perf_counter_ns",
    }
)


def _aliases(tree: ast.AST) -> dict[str, str]:
    """Map every locally bound import name to its fully qualified dotted name."""
    mapping: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mapping[alias.asname or alias.name.split(".")[0]] = (
                    alias.name if alias.asname else alias.name.split(".")[0]
                )
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                mapping[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return mapping


def _dotted(node: ast.expr, aliases: Mapping[str, str]) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(aliases.get(node.id, node.id))
    return ".".join(reversed(parts))


def _docstring_nodes(tree: ast.AST) -> set[int]:
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        ):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                found.add(id(body[0].value))
    return found


def transaction_and_clock_violations(
    source: str, *, in_migration: bool = False
) -> list[str]:
    """Transaction control, wall-clock and dynamic-escape rule.

    ``in_migration`` exempts the string-literal scan only: a migration's SQL
    legitimately contains plpgsql ``BEGIN``. Attribute and clock checks still run.
    """
    tree = ast.parse(source)
    aliases = _aliases(tree)
    docstrings = _docstring_nodes(tree)
    problems: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            if node.attr in _CONTROL_ATTRS:
                problems.append(
                    f".{node.attr} is transaction control; the caller owns it"
                )
            if node.attr in {"utcnow", "today"}:
                problems.append(f".{node.attr} reads the clock; instants are inputs")
            if node.attr in _TZ_ESCAPES:
                problems.append(f".{node.attr} reconfigures the tz database")
        if isinstance(node, ast.Name) and node.id in _TZ_ESCAPES:
            problems.append(f"{node.id} reconfigures the tz database")
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                # Whatever the alias: ``from zoneinfo import reset_tzpath as r``.
                if alias.name in _TZ_ESCAPES:
                    problems.append(
                        f"imports {alias.name}: reconfigures the tz database"
                    )
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value in _TZ_ESCAPES
            and id(node) not in docstrings
        ):
            # ``getattr(zoneinfo, "reset_tzpath")`` and any other by-name lookup.
            problems.append(f"{node.value!r} named as a string reaches the tz database")
        if isinstance(node, ast.Attribute | ast.Name):
            dotted = _dotted(node, aliases)
            if dotted in _CLOCKS:
                problems.append(f"{dotted} reads the clock; instants are inputs")
        if isinstance(node, ast.Call):
            target = _dotted(node.func, aliases)
            if target and target.rsplit(".", 1)[-1] in _SESSION_FACTORIES:
                problems.append(f"{target}( opens its own session or engine")
            func = node.func
            if isinstance(func, ast.Name) and func.id in {"getattr", "setattr"}:
                name_arg = node.args[1] if len(node.args) > 1 else None
                if not (
                    isinstance(name_arg, ast.Constant)
                    and isinstance(name_arg.value, str)
                ):
                    problems.append(f"{func.id} with a non-literal attribute name")
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_BUILTINS:
            problems.append(f"builtin {node.id} is a way around the guards")
        if (
            not in_migration
            and isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
            and _TRANSACTION_WORDS.search(node.value)
        ):
            problems.append(
                f"string literal mentions transaction control: {node.value!r}"
            )
    return problems


def dunder_violations(source: str, relative_path: str = "") -> list[str]:
    """Refuse every dunder name, attribute, definition, keyword or string.

    Docstrings are exempt. ``__all__``, ``__version__`` and ``__future__`` are
    allowed; the two declarative table names are allowed only as class-body
    assignment targets in ``models.py``.
    """
    tree = ast.parse(source)
    docstrings = _docstring_nodes(tree)
    declarative: set[int] = set()
    if relative_path == DECLARATIVE_MODULE:
        for cls in ast.walk(tree):
            if not isinstance(cls, ast.ClassDef):
                continue
            for statement in cls.body:
                if isinstance(statement, ast.Assign):
                    targets = list(statement.targets)
                elif isinstance(statement, ast.AnnAssign):
                    targets = [statement.target]
                else:
                    continue
                declarative.update(
                    id(target)
                    for target in targets
                    if isinstance(target, ast.Name) and target.id in DECLARATIVE_DUNDERS
                )
    problems: list[str] = []
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Name):
            names.append(node.id)
        elif isinstance(node, ast.Attribute):
            names.append(node.attr)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.append(node.name)
        elif isinstance(node, ast.arg):
            names.append(node.arg)
        elif isinstance(node, ast.keyword) and node.arg:
            names.append(node.arg)
        elif isinstance(node, ast.alias):
            names.extend(part for part in node.name.split("."))
            if node.asname:
                names.append(node.asname)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.extend(node.module.split("."))
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            names.append(node.value)
        for name in names:
            if (
                _DUNDER.match(name)
                and name not in ALLOWED_DUNDERS
                and id(node) not in declarative
                and not (relative_path == LOCATOR_MODULE and name in LOCATOR_DUNDERS)
            ):
                problems.append(f"dunder {name!r} is a way around the guards")
    return problems


# --- Rule 6: forbidden and restricted owner calls ---------------------------
def referenced_names(source: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.alias):
            names.add(node.name.rsplit(".", 1)[-1])
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            names.add(node.value)
    return names


def _owner_imports(source: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.ImportFrom)
            and not node.level
            and node.module in DEFAULT_DENY_OWNERS
        ):
            names.update(alias.name for alias in node.names)
    return names


def _permitted(callers: frozenset[str] | None, relative_path: str) -> bool:
    return callers is not None and ("*" in callers or relative_path in callers)


def forbidden_call_violations(source: str, relative_path: str) -> list[str]:
    problems: list[str] = []
    for name in sorted(_owner_imports(source)):
        if name == "*" or not _permitted(
            {**OWNER_NAME_ALLOWLIST, **RESTRICTED_CALLERS}.get(name), relative_path
        ):
            problems.append(
                f"{name}: not on the owner-call allowlist for {relative_path} "
                "(default deny)"
            )
    for name in sorted(referenced_names(source)):
        if FORBIDDEN_DECISION.match(name):
            problems.append(
                f"{name}: the checker is always a human via the assembly; "
                "flows never record a decision"
            )
        elif name in ALWAYS_FORBIDDEN:
            problems.append(f"{name}: not callable from flows")
        elif name in RESTRICTED_CALLERS and not _permitted(
            RESTRICTED_CALLERS[name], relative_path
        ):
            problems.append(f"{name}: not permitted in {relative_path}")
    return problems


# =============================== real tree ==================================
def test_manifest_declares_a_tenant_only_money_flows_lineage() -> None:
    assert module.code == "money_flows"
    assert module.short_code == "mflows"
    assert module.migration_prefix == "mf"
    assert module.migration_branch == "money_flows"
    assert module.db_schema == "mod_mflows"
    assert module.platform_tables == ()
    assert [
        tuple(p.value for p in planes) for planes in module.supported_plane_sets
    ] == [("tenant",)]
    assert (
        set(module.tables) == set(models.TENANT_TABLES) == set(INSTANCE_TABLE_ALLOWLIST)
    )
    assert set(module.outbox_event_types) == {
        "money_flows.settle.settled.v1",
        "money_flows.settle.attention.v1",
        "money_flows.funded_charge.discharged.v1",
        "money_flows.funded_charge.attention.v1",
        "money_flows.funded_charge.access.v1",
        "money_flows.funded_charge.reevaluate.v1",
        "money_flows.funded_charge.terms_recheck.v1",
        "money_flows.funded_charge.credit_recheck.v1",
    }
    assert set(module.requires) == {"module_database_roles.v1", "outbox_relay.v1"}
    assert module.tenant_requires == ("tenant_scope_catalog.v1",)


def test_the_physical_identity_does_not_collide_with_an_allocated_owner() -> None:
    for row in MIGRATION_OWNER_LEDGER:
        if row.owner == "money_flows":
            assert (row.prefix, row.branch_label, row.db_schema) == (
                "mf",
                "money_flows",
                "mod_mflows",
            )
        else:
            assert row.prefix != "mf"
            assert row.branch_label != "money_flows"
            assert row.db_schema != "mod_mflows"


def test_package_version_surfaces_agree() -> None:
    pyproject = tomllib.loads((PACKAGE_DIR / "pyproject.toml").read_text())
    version = pyproject["tool"]["poetry"]["version"]
    import dotmac_money_flows

    assert version == module.version == dotmac_money_flows.__version__ == "0.1.0a2"


def test_public_surface_is_the_settle_and_funded_charge_contract() -> None:
    import dotmac_money_flows

    assert set(dotmac_money_flows.__all__) == {
        "DEFAULT_ACCEPTED_SOURCES",
        "FUNDED_CHARGE_ACCESS_EVENT_TYPE",
        "FUNDED_CHARGE_ATTENTION_EVENT_TYPE",
        "FUNDED_CHARGE_CREDIT_RECHECK_EVENT_TYPE",
        "FUNDED_CHARGE_DEFINITION_VERSION",
        "FUNDED_CHARGE_DISCHARGED_EVENT_TYPE",
        "FUNDED_CHARGE_REEVALUATE_EVENT_TYPE",
        "FUNDED_CHARGE_TERMS_RECHECK_EVENT_TYPE",
        "FUNDED_CHARGE_TRANSITIONS",
        "SETTLE_ATTENTION_EVENT_TYPE",
        "SETTLE_SETTLED_EVENT_TYPE",
        "AccountCreditChangedV1",
        "AccountTermsChangedV1",
        "BalanceAppliedOutcomeV1",
        "BalancePassFailed",
        "BillingReceivablesReader",
        "CreditRecheckOutcomeV1",
        "DepositApplication",
        "FundedChargeCursorV1",
        "FundedChargeInstanceV1",
        "FundedChargeOutcomeV1",
        "FundedChargePolicyReader",
        "FundedChargePolicyV1",
        "FundedChargePorts",
        "FundedChargeRefused",
        "FundedChargeState",
        "FundedChargeStepFailed",
        "InvoiceIssuedV1",
        "RatedChargeKeyV1",
        "RatedChargeSource",
        "RatedChargeV1",
        "SettledFundsV1",
        "SettleAttentionCursorV1",
        "SettleConfirmationV1",
        "SettleConfirmationV2",
        "SettleInstanceV1",
        "SettleOutcomeV1",
        "SettlePolicyReader",
        "SettlePolicyV1",
        "SettlePorts",
        "SettleRefused",
        "SettleState",
        "SettleWithholdingV1",
        "SettlementTargetResolver",
        "SettlementTargetV1",
        "StartFundedChargeV1",
        "__version__",
        "apply_balance_on_invoice_issued",
        "charge_ref_for",
        "get_funded_charge_instance",
        "get_settle_instance",
        "is_funded_charge_replay_conflict",
        "is_settle_replay_conflict",
        "key_for_charge_ref",
        "list_funded_charge_attention",
        "list_pending_funded_charges",
        "list_settle_attention",
        "module",
        "on_account_credit_changed",
        "on_settlement_settled",
        "reevaluate_account_terms",
        "record_funded_charge_attention",
        "record_settle_refusal",
        "retry_funded_charge",
        "retry_settle",
        "settle_confirmation",
        "start_funded_charge",
        "subject_ref_for",
        "versions_dir",
    }
    assert sorted(
        p.name for p in dotmac_money_flows.versions_dir().glob("mf_*.py")
    ) == [
        "mf_0001_settle_instances.py",
        "mf_0002_funded_charge_instances.py",
        "mf_0003_settle_discharge_list.py",
    ]


def test_real_imports_use_only_the_kernel_named_owners_and_persistence() -> None:
    for path in _source_files():
        assert import_violations(path.read_text("utf-8")) == [], path


def test_real_tables_are_allowlisted_instance_tables() -> None:
    assert schema_violations(_orm_schema()) == []
    for path in sorted(MIGRATIONS.glob("mf_*.py")):
        source = path.read_text("utf-8")
        created = migration_schema(source)
        added = migration_added_columns(source)
        assert created or added, f"{path.name} creates no table and adds no column"
        assert schema_violations(created) == [], path
        assert added_column_violations(added) == [], path
        assert foreign_key_violations(source) == [], path
        if created:
            assert migration_security_violations(source) == [], path
    additive = migration_added_columns(
        (MIGRATIONS / "mf_0003_settle_discharge_list.py").read_text("utf-8")
    )
    assert additive == {"settle_instances": {"withholding_discharge_list_ref": "JSON"}}
    for table, migration in (
        ("settle_instances", "mf_0001_settle_instances.py"),
        ("funded_charge_instances", "mf_0002_funded_charge_instances.py"),
    ):
        expected = set(
            migration_schema((MIGRATIONS / migration).read_text("utf-8"))[table]
        ) | set(additive.get(table, {}))
        assert set(_orm_schema()[table]) == expected


def test_orm_foreign_keys_target_only_tenants_or_own_schema() -> None:
    for model in models.TENANT_MODELS:
        for constraint in model.__table__.foreign_key_constraints:
            for element in constraint.elements:
                target = element.column.table
                assert (target.schema, target.name) in {
                    (None, "tenants"),
                    ("public", "tenants"),
                } or target.schema == models.SCHEMA, element.target_fullname


def test_real_package_has_no_product_or_provider_literals() -> None:
    for path in _source_files():
        assert literal_violations(path.read_text("utf-8")) == [], path


def test_real_package_never_controls_transactions_or_reads_a_clock() -> None:
    for path in _source_files():
        found = transaction_and_clock_violations(
            path.read_text("utf-8"), in_migration="migrations" in path.parts
        )
        assert found == [], path


def test_real_package_uses_no_dunder_name_or_attribute() -> None:
    for path in _source_files():
        assert dunder_violations(path.read_text("utf-8"), _relative(path)) == [], path


def test_real_package_references_no_forbidden_or_restricted_owner_call() -> None:
    for path in _source_files():
        assert forbidden_call_violations(path.read_text("utf-8"), _relative(path)) == []
    # Only the credit draw (settle.py, funded_charge.py) and the rated invoice
    # issue (funded_charge.py) are allowlisted. PR E adds the F3/F5 modules
    # deliberately.
    assert {n for n, callers in RESTRICTED_CALLERS.items() if callers} == {
        "apply_available_credit",
        "ApplyAvailableCreditCommand",
        "issue_rated_invoice",
    }
    both = frozenset({"settle.py", "funded_charge.py"})
    assert RESTRICTED_CALLERS["apply_available_credit"] == both
    assert RESTRICTED_CALLERS["ApplyAvailableCreditCommand"] == both
    assert RESTRICTED_CALLERS["issue_rated_invoice"] == frozenset({"funded_charge.py"})
    for callers in RESTRICTED_CALLERS.values():
        for relative in callers:
            assert (MODULE_ROOT / relative).is_file(), relative


def test_the_owner_allowlist_names_only_real_public_owner_exports() -> None:
    public = {owner: _owner_all(owner) or frozenset() for owner in DEFAULT_DENY_OWNERS}
    everything = frozenset().union(*public.values())
    for name in (*OWNER_NAME_ALLOWLIST, *RESTRICTED_CALLERS):
        assert name in everything, f"{name} is in no allowed owner's __all__"
    # Every reversal and credit command the owner exports is explicitly decided.
    for name in public["dotmac_billing"]:
        if name.startswith("reverse_"):
            assert name in RESTRICTED_CALLERS, f"{name} has no allowlist decision"


def test_the_money_flows_contract_forbids_every_other_root_package() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["tool"]
    linter = config["importlinter"]
    contract = next(
        c
        for c in linter["contracts"]
        if c["name"] == "Modules and connectors must not import money flows"
    )
    expected = set(linter["root_packages"]) - {"app", "dotmac_money_flows"}
    assert set(contract["source_modules"]) == expected
    assert len(contract["source_modules"]) == len(set(contract["source_modules"]))
    assert contract["forbidden_modules"] == ["dotmac_money_flows"]


# ============================ planted violations ============================
@pytest.mark.parametrize(
    "source, needle",
    [
        ("from dotmac_billing.service import accept_settlement", "top-level"),
        ("import dotmac_billing.service", "top-level"),
        ("import dotmac_billing", "named public names"),
        (
            "from dotmac_billing import not_a_public_name_xyz",
            "not in dotmac_billing.__all__",
        ),
        ("from dotmac_collections import evaluate_grace", "contracts only"),
        ("from dotmac_sap import anything", "not the kernel"),
        (
            "from dotmac_service_access_policy import record_access_input_v2",
            "contracts only",
        ),
        (
            "from dotmac_service_access_policy import not_a_public_name_xyz",
            "not in dotmac_service_access_policy.__all__",
        ),
        ("from dotmac_service_access_policy.service import x", "top-level"),
        ("from dotmac_kernel._internal import thing", "private kernel module"),
        ("from dotmac_sub.app import thing", "not the kernel"),
        ("import httpx", "not the kernel"),
        ("from app.main import app", "not the kernel"),
        ("from dotmac_workflow_runtime import start_execution", "not the kernel"),
    ],
)
def test_planted_import_violations_are_refused(source: str, needle: str) -> None:
    assert any(needle in problem for problem in import_violations(source)), source


def test_the_import_rule_accepts_legitimate_imports() -> None:
    clean = (
        "from __future__ import annotations\n"
        "from datetime import datetime\n"
        "from dotmac_kernel.models import Base\n"
        "from dotmac_billing import AcceptSettlementV1\n"
        "from dotmac_collections import ReceivableObservationV1\n"
        "import sqlalchemy as sa\n"
        "from dotmac_money_flows.models import SettleInstance\n"
    )
    assert import_violations(clean) == []


_COMMON = {name: "String" for name in models.COMMON_COLUMNS}


@pytest.mark.parametrize(
    "tables, needle",
    [
        ({"rogue_instances": dict(_COMMON)}, "not in INSTANCE_TABLE_ALLOWLIST"),
        ({"settle_instances": {**_COMMON, "net_amount": "String"}}, "forbidden word"),
        ({"settle_instances": {**_COMMON, "paid_total": "String"}}, "forbidden word"),
        (
            {"settle_instances": {**_COMMON, "lease_owner_ref": "String"}},
            "forbidden word",
        ),
        ({"settle_instances": {**_COMMON, "retry_count": "Integer"}}, "forbidden word"),
        (
            {"settle_instances": {**_COMMON, "dead_letter_ref": "String"}},
            "forbidden word",
        ),
        ({"settle_instances": {**_COMMON, "timer_id": "Uuid"}}, "forbidden word"),
        ({"settle_instances": {**_COMMON, "note": "String"}}, "neither a common"),
        ({"settle_instances": {**_COMMON, "other_id": "Numeric"}}, "money-capable"),
        ({"settle_instances": {**_COMMON, "other_ref": "Float"}}, "money-capable"),
        ({"settle_instances": {"id": "Uuid"}}, "lacks common columns"),
    ],
)
def test_planted_schema_violations_are_refused(
    tables: dict[str, dict[str, str]], needle: str
) -> None:
    assert any(needle in p for p in schema_violations(tables)), tables


def test_the_schema_rule_accepts_declared_refs_and_the_common_set() -> None:
    assert (
        schema_violations(
            {"settle_instances": {**_COMMON, "intent_id": "Uuid", "x_ref": "String"}}
        )
        == []
    )


def test_a_planted_migration_table_is_found_by_the_migration_walk() -> None:
    source = (
        "import sqlalchemy as sa\nfrom alembic import op\n"
        "def upgrade():\n"
        "    op.create_table('rogue_balances', sa.Column('id', sa.Uuid()),"
        " sa.Column('amount', sa.Numeric(20, 6)))\n"
    )
    found = migration_schema(source)
    assert found == {"rogue_balances": {"id": "Uuid", "amount": "Numeric"}}
    assert schema_violations(found)


def test_planted_foreign_keys_to_another_owner_are_refused() -> None:
    assert foreign_key_violations('X = ["mod_billing.documents.id"]')
    assert foreign_key_violations('X = ["public.accounts.id"]')
    assert foreign_key_violations('X = ["mod_mflows.settle_instances.id"]') == []
    assert foreign_key_violations('X = ["public.tenants.id"]') == []


def test_planted_migration_security_violations_are_refused() -> None:
    assert migration_security_violations("GRANT SELECT ON t TO app_user")
    assert migration_security_violations(
        "FORCE ROW LEVEL SECURITY; GRANT SELECT, DELETE ON t TO app_user;"
    )
    assert migration_security_violations(
        "FORCE ROW LEVEL SECURITY; GRANT ALL ON t TO app_user;"
    )
    assert (
        migration_security_violations(
            "FORCE ROW LEVEL SECURITY; GRANT SELECT, INSERT, UPDATE ON t TO app_user;"
        )
        == []
    )


@pytest.mark.parametrize(
    "source",
    [
        "x = 'paystack'",
        "provider = 'Flutterwave'",
        "def is_paystack(x): return x",
        "if is_remita: pass",
        "class SubSettlement: pass",
        "x = 'erp_sync'",
        "whatsapp = 1",
    ],
)
def test_planted_product_literals_are_refused(source: str) -> None:
    assert literal_violations(source), source


def test_the_literal_scan_does_not_flag_ordinary_words() -> None:
    assert literal_violations("x = 'metadata'\nsubstitute = 'monotonic'") == []


@pytest.mark.parametrize(
    "source",
    [
        "db.commit()",
        "session.rollback()",
        "db.begin_nested()",
        "hook = db.commit",  # a reference, not a call
        "fns = [session.rollback]",
        "datetime.now()",
        "datetime.datetime.now(tz)",
        "datetime.utcnow()",
        "date.today()",
        "import datetime as dt\ndt.datetime.now()",
        "from datetime import datetime as D\nD.now()",
        "from datetime import datetime as D\nclock = D.utcnow",
        "from datetime import date as d\nd.today()",
        "import time\ntime.time()",
        "import time as t\nt.monotonic()",
        "from time import perf_counter as pc\npc()",
        "from time import time as now_fn\nnow_fn()",
        "import time\ntime.monotonic_ns()",
        "db.execute(text('COMMIT'))",
        "db.execute('rollback')",
        "q = 'SAVEPOINT s1'",
        "q = 'BEGIN'",
        "engine.connect()",
        "Session(engine)",
        "from sqlalchemy.orm import sessionmaker\nsessionmaker(bind=e)",
        "from sqlalchemy.orm import Session as S\nS(e)",
        "create_engine(url)",
        "import zoneinfo\nzoneinfo.reset_tzpath(['x'])",
        "from zoneinfo import reset_tzpath\nreset_tzpath()",
        "from zoneinfo import ZoneInfo\nZoneInfo.clear_cache()",
        "from zoneinfo import ZoneInfo\nZoneInfo.from_file(f)",
        "hook = zoneinfo.reset_tzpath",  # a reference, not a call
        "from zoneinfo import reset_tzpath as r\nr()",
        "from zoneinfo import ZoneInfo as Z\nfrom zoneinfo import clear_cache as c",
        'import zoneinfo\ngetattr(zoneinfo, "reset_tzpath")()',
        'import zoneinfo\nzoneinfo.__dict__["clear_cache"]',
    ],
)
def test_planted_transaction_control_and_clock_reads_are_refused(source: str) -> None:
    assert transaction_and_clock_violations(source), source


@pytest.mark.parametrize(
    "source",
    [
        "__import__('os')",
        "eval('1')",
        "exec('x = 1')",
        "globals()['x']",
        "vars(obj)",
        "getattr(db, name)",
        "setattr(db, 'a' + 'b', 1)",
        "getattr(db, attr)()",
    ],
)
def test_planted_dynamic_escapes_are_refused(source: str) -> None:
    assert transaction_and_clock_violations(source), source


@pytest.mark.parametrize(
    "source",
    [
        "locals()",
        "compile('1', 'f', 'eval')",
        "breakpoint()",
        "x = locals",
    ],
)
def test_planted_locals_compile_and_breakpoint_are_refused(source: str) -> None:
    assert transaction_and_clock_violations(source), source


@pytest.mark.parametrize(
    "source",
    [
        "x.__class__",
        "x.__dict__",
        "type(x).__mro__",
        "().__class__.__bases__[0].__subclasses__()",
        "__builtins__",
        "__file__",
        "__name__",
        "x.__globals__",
        "f.__code__",
        "x.__getattribute__('y')",
        "def __getattr__(self, name): return 1",
        "class __Hidden__: pass",
        "f(__x__=1)",
        "def f(__x__): return 1",
        "getattr(x, '__class__')",
        "import foo.__init__",
        "from __main__ import x",
        "from . import __init__",
        "__tablename__ = 'rogue'",  # outside models.py
        "def f():\n    __tablename__ = 'x'",  # not a class body
        "class A:\n    __tablename__ = 'x'\n    y = __tablename__",  # a read
        "class A:\n    __slots__ = ()",
    ],
)
def test_planted_dunders_are_refused(source: str) -> None:
    assert dunder_violations(source, "settle.py"), source


def test_the_dunder_rule_allows_only_module_plumbing_and_declarative_tables() -> None:
    assert dunder_violations("__all__ = ['x']\n__version__ = '1'") == []
    assert dunder_violations("from __future__ import annotations") == []
    assert dunder_violations('"""Mentions __class__ in a docstring."""\nx = 1') == []
    declared = "class A:\n    __tablename__ = 'x'\n    __table_args__ = ()"
    assert dunder_violations(declared, "models.py") == []
    assert dunder_violations(declared, "settle.py")
    read = "class A:\n    __tablename__ = 'x'\n    y = __tablename__"
    assert dunder_violations(read, "models.py")
    locator = "from pathlib import Path\nx = Path(__file__)"
    assert dunder_violations(locator, "migrations/__init__.py") == []
    assert dunder_violations(locator, "settle.py")
    assert dunder_violations("x = __class__", "migrations/__init__.py")


def test_a_literal_attribute_name_is_not_a_dynamic_escape() -> None:
    assert transaction_and_clock_violations("getattr(obj, 'name')") == []


def test_the_database_clock_and_migration_sql_are_not_violations() -> None:
    assert transaction_and_clock_violations("x = sa.func.now()\ny = func.now()") == []
    sql = "op.execute('CREATE FUNCTION f() AS $$ BEGIN RETURN NEW; END; $$')"
    assert transaction_and_clock_violations(sql, in_migration=True) == []
    # ...but the exemption is for the string scan only.
    assert transaction_and_clock_violations(sql + "\ndb.commit()", in_migration=True)
    assert transaction_and_clock_violations(sql, in_migration=False)
    docstring = '"""BEGIN here, then COMMIT."""\nx = 1'
    assert transaction_and_clock_violations(docstring) == []


@pytest.mark.parametrize(
    "source",
    [
        "import importlib",
        "from importlib import import_module",
        "import sys",
        "import os",
        "import subprocess",
        "import ast",
        "from pickle import loads",
        "from dotmac_billing import *",
        "from dotmac_collections import *",
        "from sqlalchemy import *",
    ],
)
def test_planted_unlisted_stdlib_and_wildcard_imports_are_refused(source: str) -> None:
    assert import_violations(source), source


@pytest.mark.parametrize(
    "source",
    [
        "from dotmac_approvals import record_tenant_decision",
        "approvals.record_platform_decision(db)",
        "record_anything_decision(x)",
        "seal_opening_lane(db)",
        "from dotmac_billing import issue_document",
        "x = IssueDocument(...)",
        "getattr(billing, 'issue_document')",
    ],
)
def test_planted_always_forbidden_calls_are_refused(source: str) -> None:
    assert forbidden_call_violations(source, "settle.py"), source


@pytest.mark.parametrize("name", sorted(RESTRICTED_CALLERS))
def test_restricted_commands_are_refused_outside_their_allowlist(name: str) -> None:
    if "settle.py" not in RESTRICTED_CALLERS[name]:
        assert forbidden_call_violations(f"billing.{name}(db)", "settle.py"), name
    assert forbidden_call_violations(f"billing.{name}(db)", "adjust.py"), name
    imported = f"from dotmac_billing import {name}\n{name}(db)"
    assert forbidden_call_violations(imported, "adjust.py"), name


@pytest.mark.parametrize(
    "source",
    [
        "from dotmac_billing import refund_settlement",
        "from dotmac_billing import deallocate_settlement",
        "from dotmac_billing import a_command_added_next_year",
        "from dotmac_payments import record_confirmation",
        "from dotmac_approvals import request_tenant_approval",
        "from dotmac_billing import *",
        "from dotmac_service_access_policy import record_access_input_v2",
        "from dotmac_service_access_policy import ServiceAccessInput",
        "from dotmac_service_access_policy import decide_access_input",
        "from dotmac_service_access_policy import *",
    ],
)
def test_an_unlisted_owner_name_is_denied_by_default(source: str) -> None:
    assert forbidden_call_violations(source, "settle.py"), source


def test_the_credit_draw_is_accepted_in_settle_and_funded_charge_only() -> None:
    source = (
        "from dotmac_billing import apply_available_credit, "
        "ApplyAvailableCreditCommand\n"
        "apply_available_credit(db)\n"
    )
    assert forbidden_call_violations(source, "settle.py") == []
    assert forbidden_call_violations(source, "funded_charge.py") == []
    assert forbidden_call_violations(source, "adjust.py")


def test_the_rated_invoice_issue_is_accepted_in_funded_charge_only() -> None:
    source = "from dotmac_billing import issue_rated_invoice\nissue_rated_invoice(db)\n"
    assert forbidden_call_violations(source, "funded_charge.py") == []
    assert forbidden_call_violations(source, "settle.py")
    assert forbidden_call_violations(source, "adjust.py")
    assert forbidden_call_violations("billing.issue_rated_invoice(db)", "settle.py")


@pytest.mark.parametrize(
    "source, relative",
    [
        ("from dotmac_billing import issue_rated_invoice", "settle.py"),
        ("from dotmac_billing import apply_available_credit", "adjust.py"),
        ("from dotmac_billing import get_effective_account_terms", "adjust.py"),
        ("from dotmac_billing import get_funding_availability", "adjust.py"),
        ("from dotmac_billing import AccountTermsV1", "adjust.py"),
        ("from dotmac_billing import GetFundingAvailabilityV1", "adjust.py"),
        ("from dotmac_billing import get_confirmed_settlement", "settle.py"),
        ("from dotmac_billing import IssueRatedInvoiceV3", "settle.py"),
        ("from dotmac_service_access_policy import AccessSignal", "settle.py"),
        ("from dotmac_service_access_policy import RecordAccessInputV2", "settle.py"),
    ],
)
def test_funded_charge_only_names_are_refused_in_other_modules(
    source: str, relative: str
) -> None:
    assert forbidden_call_violations(source, relative), source
    if relative != "funded_charge.py" and "apply_available_credit" not in source:
        assert forbidden_call_violations(source, "funded_charge.py") == [], source


def test_terms_and_availability_reads_are_settle_and_funded_charge_only() -> None:
    names = (
        "get_effective_account_terms",
        "AccountTermsV1",
        "get_funding_availability",
        "GetFundingAvailabilityV1",
    )
    for name in names:
        source = f"from dotmac_billing import {name}\n"
        assert forbidden_call_violations(source, "settle.py") == [], name
        assert forbidden_call_violations(source, "funded_charge.py") == [], name
        assert forbidden_call_violations(source, "adjust.py"), name


@pytest.mark.parametrize(
    "name",
    ["ReceivableExposureV1", "ReceivableExposureV2", "DueDateBasisStatus"],
)
def test_exposure_types_are_readable_only_from_the_x1_reader(name: str) -> None:
    source = f"from dotmac_billing import {name}\n"
    assert forbidden_call_violations(source, "receivables.py") == [], name
    for other in ("settle.py", "funded_charge.py", "adjust.py"):
        assert forbidden_call_violations(source, other), (name, other)


def test_the_x1_reader_may_read_terms_but_not_availability_or_commands() -> None:
    allowed = "from dotmac_billing import get_effective_account_terms\n"
    assert forbidden_call_violations(allowed, "receivables.py") == []
    for name in (
        "get_funding_availability",
        "apply_available_credit",
        "issue_rated_invoice",
    ):
        source = f"from dotmac_billing import {name}\n"
        assert forbidden_call_violations(source, "receivables.py"), name


def test_the_x1_reader_imports_only_collections_contracts() -> None:
    real = (MODULE_ROOT / "receivables.py").read_text("utf-8")
    assert "dotmac_collections" in real
    assert import_violations(real) == []
    planted = real + "\nfrom dotmac_collections import evaluate_grace\n"
    assert any("contracts only" in p for p in import_violations(planted))
    assert any(
        "contracts only" in p
        for p in import_violations(
            "from dotmac_collections import publish_policy_version"
        )
    )


#: The only modules that may use the dating module (and so the statement read).
DATING_CALLERS = frozenset({"settle.py", "funded_charge.py"})
DATING_NAMES = frozenset({"_credit_dating", "newest_credit_at"})


def dating_reference_violations(source: str, relative_path: str) -> list[str]:
    """A module outside ``DATING_CALLERS`` may not reach the statement read through
    the dating module (``newest_credit_at``)."""
    if relative_path in DATING_CALLERS | {"_credit_dating.py"}:
        return []
    return [
        f"{name}: only {sorted(DATING_CALLERS)} may use the credit dating module"
        for name in sorted(referenced_names(source) & DATING_NAMES)
    ]


def test_only_settle_and_funded_charge_reach_the_credit_dating_module() -> None:
    for source in (
        "from dotmac_money_flows import _credit_dating",
        "from dotmac_money_flows._credit_dating import newest_credit_at",
        "import dotmac_money_flows._credit_dating",
        "newest_credit_at(db)",
        "dating.newest_credit_at(db)",
    ):
        assert dating_reference_violations(source, "adjust.py"), source
        assert dating_reference_violations(source, "settle.py") == [], source
        assert dating_reference_violations(source, "funded_charge.py") == [], source
    modules = {
        _relative(path)
        for path in _source_files()
        if referenced_names(path.read_text("utf-8")) & DATING_NAMES
    }
    assert modules <= DATING_CALLERS | {"_credit_dating.py"}
    assert DATING_CALLERS <= modules  # non-vacuous: both really use it
    for path in _source_files():
        assert (
            dating_reference_violations(path.read_text("utf-8"), _relative(path)) == []
        )


def test_the_account_statement_read_is_credit_dating_only() -> None:
    for name in ("get_account_statement", "GetAccountStatementV1"):
        source = f"from dotmac_billing import {name}\n"
        assert forbidden_call_violations(source, "_credit_dating.py") == [], name
        assert forbidden_call_violations(source, "settle.py"), name
        assert forbidden_call_violations(source, "funded_charge.py"), name
        assert forbidden_call_violations(source, "adjust.py"), name
    # The real tree: no module but the dating module names the statement read.
    for path in _source_files():
        if _relative(path) == "_credit_dating.py":
            continue
        names = referenced_names(path.read_text("utf-8"))
        assert not names & {"get_account_statement", "GetAccountStatementV1"}, path


def test_the_listed_owner_calls_are_accepted() -> None:
    source = (
        "from dotmac_billing import accept_settlement, AcceptSettlementV1\n"
        "from dotmac_payments import get_confirmed_cash_v1\n"
    )
    assert forbidden_call_violations(source, "settle.py") == []


def test_service_access_policy_is_contracts_only_and_default_deny() -> None:
    assert "dotmac_service_access_policy" in CONTRACT_ONLY_OWNERS
    assert "dotmac_service_access_policy" in DEFAULT_DENY_OWNERS
    assert "dotmac_sap" not in OWNERS
    allowed = sorted(
        name
        for name in OWNER_NAME_ALLOWLIST
        if name in (_owner_all("dotmac_service_access_policy") or frozenset())
    )
    assert allowed == ["AccessSignal", "RecordAccessInputV2"]
    accepted = (
        "from dotmac_service_access_policy import RecordAccessInputV2, AccessSignal\n"
    )
    assert import_violations(accepted) == []
    assert forbidden_call_violations(accepted, "funded_charge.py") == []


def test_a_restricted_command_is_accepted_only_in_an_allowlisted_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allowed = {**RESTRICTED_CALLERS, "void_document": frozenset({"adjust.py"})}
    monkeypatch.setitem(globals(), "RESTRICTED_CALLERS", allowed)
    source = "from dotmac_billing import void_document\nvoid_document(db)"
    assert forbidden_call_violations(source, "adjust.py") == []
    assert forbidden_call_violations(source, "settle.py")
    # An allowlist for one command never opens another.
    other = "from dotmac_billing import issue_credit_note"
    assert forbidden_call_violations(other, "adjust.py")


def test_ordinary_decision_words_are_not_flagged() -> None:
    assert (
        forbidden_call_violations("decision = 1\nrecord_settle_refusal(db)", "a.py")
        == []
    )


# --- F2 reads the tenant policy in exactly one place (GAP-5 review) ----------
def policy_read_sites(source: str) -> list[str]:
    """Names of the functions that call ``<anything>.policy.read(...)``."""
    sites: list[str] = []

    def walk(node: ast.AST, owner: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = (
                child.name
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)
                else owner
            )
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr == "read"
                and isinstance(child.func.value, ast.Attribute)
                and child.func.value.attr == "policy"
            ):
                sites.append(owner)
            walk(child, name)

    walk(ast.parse(source), "<module>")
    return sites


def test_the_f2_policy_is_read_only_through_the_validating_helper() -> None:
    source = (MODULE_ROOT / "funded_charge.py").read_text("utf-8")
    assert policy_read_sites(source) == ["_read_policy"]


@pytest.mark.parametrize(
    "source",
    [
        "def f(ports, db, scope):\n    return ports.policy.read(db, scope=scope)",
        "def _read_policy(p):\n    p.policy.read()\n"
        "def g(ports):\n    return ports.policy.read()",
        "class C:\n    def m(self):\n        return self.ports.policy.read(1)",
    ],
)
def test_a_planted_unvalidated_policy_read_is_found(source: str) -> None:
    assert policy_read_sites(source) != ["_read_policy"]
