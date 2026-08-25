"""This repository's own guards must carry three legs — and the gap is frozen.

ADR-0018's 2026-08-15 amendment says every detector ARM proves SENSITIVITY (a
representative violation fires), SPECIFICITY (the near-miss stays silent) and
LIVENESS (the arm reaches and correctly classifies REAL corpus code). The ADR
adds, honestly, that retrofitting the existing suite "is a programme, not a
gate".

A programme with no gate is a wish. This module is the gate: it MEASURES the
retrofit backlog from the real tree and freezes it exactly, so the programme can
run one guard at a time while it stays impossible to add a sixty-second
unproved arm on the way — and impossible to quietly retire one either.

Measured 2026-08-15 across the 50 guard modules in `tests/architecture/`:
**23 fixture-only arms, 22 guards with no firing proof at all, 16 vacuous
discovery scopes.**

## The subject is `tests/architecture/*.py`, including this file

The guard suite audits itself. This module is inside its own corpus: if the
ratchet's own arms were fixture-only, the ratchet would report itself. It does
not, because its three arms each carry an in-situ liveness leg below.

## The three populations

Each is measured from the real bytes of the real guard suite. The names are the
ADR's, and the unit of each is chosen so the backlog is ACTIONABLE — a named
arm to fix, not a number to admire.

**1. `fixture_only_arms`** — keyed `<guard file>::<detector>`. A detector that
some test drives with author-chosen fixture data, and that NOTHING drives over
mutated real corpus bytes. Two legs, no third. The value lists the proofs that
exist, so the retrofit knows where to add the leg.

*Why a mutation-driven call and not merely a corpus-driven one.* The production
assertion (`assert not offenders` over the real tree) already hands the
classifier the corpus, and it is exactly the shape the ADR indicts: Governance's
factory arm resolved ZERO real spellings while its production assertion ran
green over 5,626 sources. Reaching the corpus is not classifying it. Liveness
credit therefore requires a call whose argument is real corpus bytes JOINED WITH
a planted violation — the in-situ mutation the ADR prescribes. Credit is
transitive through the call graph: a helper exercised by a mutation-driven
parent is exercised.

**2. `guards_without_a_firing_proof`** — keyed by guard file. The file scans the
real tree and NOTHING anywhere in it ever plants a violation. Not two legs: no
legs. A clean run here is indistinguishable from a guard that stopped looking,
which is ADR-0018 decision point 5 verbatim. The value names the file's
discovery handles, so the retrofit knows what to plant against.

**3. `vacuous_discovery_scopes`** — keyed `<guard file>::<scope>::<call>`. A
`glob`/`rglob`/`iterdir` over a real-tree path whose result is never asserted
non-empty. `for path in root.rglob("*.py"): assert ...` over zero paths is a
green run and a silent one; a renamed directory retires the guard without
retiring the test. `test_thin_wrappers.py::test_router_scan_is_not_vacuous` is
the in-repo counter-example — one assertion is all a site needs.

## Two-directional (ADR-0018 decision point 3)

The gate fails when a population GROWS and when it SHRINKS. Growth is new
unmonitored debt. A silent shrink is just as unreviewable: it is either a real
retrofit that nobody reviewed, or — far more likely — an arm that was deleted,
a guard that was renamed, or a discovery glob that stopped matching. Every one
of those must land as a visible diff to `guard_proof_backlog.json`::

    make guard-proof-baseline

## What this gate deliberately is NOT

It does not judge whether a guard is *correct*, and it cannot. It measures
whether anything ever drove the guard's detector at a violation, and whether
anything ever drove it at the subject. Those are the two failures that pass
review, pass CI, and report coverage while seeing nothing.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Final

PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
GUARD_DIR: Final[Path] = PROJECT_ROOT / "tests" / "architecture"
BASELINE_PATH: Final[Path] = Path(__file__).parent / "guard_proof_backlog.json"

#: Attribute calls that read the real filesystem. `os.walk` is deliberately
#: absent: `ast.walk` shares the spelling, and treating every AST traversal as
#: a corpus read made every pure classifier look like a scanner (measured — it
#: collapsed population 1 from 23 arms to 6 before the bug was found).
FS_READ: Final[frozenset[str]] = frozenset(
    {"glob", "rglob", "iterdir", "read_text", "read_bytes"}
)

#: Discovery spellings whose emptiness is invisible without an assertion.
DISCOVERY_CALLS: Final[frozenset[str]] = frozenset({"glob", "rglob", "iterdir"})

#: `re` methods, so a module-level compiled pattern counts as a detector arm in
#: its own right — several guards in this suite ARE a regex plus a loop.
MATCHER_CALLS: Final[frozenset[str]] = frozenset(
    {"search", "match", "fullmatch", "findall", "finditer"}
)

#: String methods that locate an author-chosen marker inside real bytes. The
#: result is the fixture's offset, so slicing on it is a deliberate cut.
LOCATOR_CALLS: Final[frozenset[str]] = frozenset({"index", "find", "rindex", "rfind"})

#: String methods that rewrite real bytes using a fixture — in-situ mutation
#: spelled as an edit rather than a concatenation.
SURGERY_CALLS: Final[frozenset[str]] = frozenset(
    {"replace", "removeprefix", "removesuffix"}
)

LITERAL: Final[str] = "LITERAL"
CORPUS: Final[str] = "CORPUS"
MUTATED: Final[str] = "MUTATED"
UNKNOWN: Final[str] = "UNKNOWN"


# ---------------------------------------------------------------------------
# Corpus roots: which names in a guard hold a path into the real tree
# ---------------------------------------------------------------------------


def _bound_names(target: ast.expr) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, ast.Tuple | ast.List):
        names: set[str] = set()
        for element in target.elts:
            names |= _bound_names(element)
        return names
    return set()


def _looks_like_a_path(value: ast.expr, known: set[str]) -> bool:
    for node in ast.walk(value):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "Path":
                return True
        if isinstance(node, ast.Name) and node.id in known:
            return True
        if isinstance(node, ast.Attribute) and node.attr in FS_READ:
            return True
    return False


def module_roots(tree: ast.Module) -> set[str]:
    """Module-level names holding a real-tree path.

    Module level ONLY. The whole-tree variant below is right for finding
    discovery sites and wrong for origin analysis: a function-local `source`
    in one test would otherwise mark an identically-named local in another as
    corpus-derived.
    """
    roots: set[str] = set()
    changed = True
    while changed:
        changed = False
        for node in tree.body:
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets, value = [node.target], node.value
            else:
                continue
            if not _looks_like_a_path(value, roots):
                continue
            for target in targets:
                for name in _bound_names(target):
                    if name not in roots:
                        roots.add(name)
                        changed = True
    return roots


def path_names(tree: ast.Module) -> set[str]:
    """Every name anywhere that may hold a real-tree path.

    Includes `for root in template_roots():` targets and comprehension
    generators, because `root.rglob(...)` inside the loop is the commonest
    discovery spelling in this suite and binding it through the loop is the
    only way to see it.
    """
    names = module_roots(tree)
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets, value = [node.target], node.value
            elif isinstance(node, ast.For | ast.AsyncFor):
                targets, value = [node.target], node.iter
            elif isinstance(node, ast.comprehension):
                targets, value = [node.target], node.iter
            else:
                continue
            if not _looks_like_a_path(value, names):
                continue
            for target in targets:
                for name in _bound_names(target):
                    if name not in names:
                        names.add(name)
                        changed = True
    return names


# ---------------------------------------------------------------------------
# Helpers, and which of them read the corpus themselves
# ---------------------------------------------------------------------------


def helper_functions(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    """Every non-test `def`, nested ones included.

    Nested matters: `test_the_safe_filter_guard_still_bites` defines its
    detector wrapper inside the test body, and an arm defined in a test is
    still an arm.
    """
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("test_")
    }


def _reads_corpus(
    fn: ast.FunctionDef,
    roots: set[str],
    helpers: dict[str, ast.FunctionDef],
    seen: set[str] | None = None,
) -> bool:
    seen = set() if seen is None else seen
    if fn.name in seen:
        return False
    seen.add(fn.name)
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and node.id in roots:
            return True
        if isinstance(node, ast.Attribute) and node.attr in FS_READ:
            return True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            nested = helpers.get(node.func.id)
            if nested is not None and _reads_corpus(nested, roots, helpers, seen):
                return True
    return False


def _is_selector(fn: ast.FunctionDef, roots: set[str]) -> bool:
    """Does a literal handed to this helper name a PLACE rather than carry bytes?

    `_allowlist_entry("dotmac-files")` and `_load_toml(path)` take a selector:
    the argument is spent building or reading a path, so the literal is a
    lookup key and counting it as a planted violation would fill the backlog
    with noise. `_validate_dossier(dossier, ...)` takes a payload, even though
    it happens to touch `PROJECT_ROOT` while checking that a cited file
    exists — which is why "does the helper read the tree" is the wrong
    question here, and cost this measurement a real three-leg guard
    (`test_product_first_extraction.py`) before it was asked properly.
    """
    params = {
        argument.arg
        for group in (fn.args.posonlyargs, fn.args.args, fn.args.kwonlyargs)
        for argument in group
    }
    if not params:
        return False
    for node in ast.walk(fn):
        if isinstance(node, ast.Attribute) and node.attr in FS_READ:
            if _names_in(node.value) & params:
                return True
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            if (_names_in(node) & params) and _looks_like_a_path(node, roots):
                return True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "Path" and any(
                _names_in(argument) & params for argument in node.args
            ):
                return True
    return False


def _call_graph(helpers: dict[str, ast.FunctionDef]) -> dict[str, set[str]]:
    return {
        name: {
            node.func.id
            for node in ast.walk(fn)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in helpers
            and node.func.id != name
        }
        for name, fn in helpers.items()
    }


# ---------------------------------------------------------------------------
# Origin analysis: where did the bytes a detector was handed come from?
# ---------------------------------------------------------------------------


class _Origins:
    """Track, inside one test, whether a value is a fixture or real bytes.

    Four origins. `LITERAL` is a fixture the author wrote. `CORPUS` came off
    disk. `MUTATED` is corpus bytes JOINED WITH a literal — the in-situ
    mutation ADR-0018 asks for, and the only origin that earns a liveness leg.
    `UNKNOWN` is everything this deliberately shallow analysis will not guess
    at, and it earns nothing.
    """

    def __init__(
        self,
        fn: ast.FunctionDef,
        roots: set[str],
        helpers: dict[str, ast.FunctionDef],
        scanners: set[str],
        selectors: set[str],
        module_constants: set[str],
    ) -> None:
        self.roots = roots
        self.helpers = helpers
        self.scanners = scanners
        self.selectors = selectors
        self.constants = module_constants
        self.env: dict[str, str] = {}
        self.calls: list[tuple[str, tuple[str, ...]]] = []
        for decorator in fn.decorator_list:
            if isinstance(decorator, ast.Call) and "parametrize" in ast.dump(
                decorator.func
            ):
                # Parametrised cases are fixtures the author chose, so the
                # parameters carry fixture origin.
                for argument in fn.args.args:
                    self.env[argument.arg] = LITERAL
        self._visit(fn)

    def origin(self, node: ast.expr | None) -> str:
        if node is None:
            return UNKNOWN
        if isinstance(node, ast.Constant | ast.JoinedStr):
            return LITERAL
        if isinstance(node, ast.Dict | ast.List | ast.Set | ast.Tuple):
            elements = (
                [v for v in node.values if v is not None]
                if isinstance(node, ast.Dict)
                else list(node.elts)
            )
            return self._combine({self.origin(e) for e in elements}, empty=LITERAL)
        if isinstance(node, ast.BinOp):
            left, right = self.origin(node.left), self.origin(node.right)
            if MUTATED in (left, right):
                return MUTATED
            if CORPUS in (left, right):
                # Corpus bytes concatenated with a fixture: the in-situ plant.
                return MUTATED if LITERAL in (left, right) else CORPUS
            return left if left == right else UNKNOWN
        if isinstance(node, ast.Name):
            return CORPUS if node.id in self.roots else self.env.get(node.id, UNKNOWN)
        if isinstance(node, ast.Subscript):
            base = self.origin(node.value)
            if base in (CORPUS, MUTATED) and isinstance(node.slice, ast.Slice):
                # A SLICE of real bytes bounded by an author-chosen marker is
                # an in-situ mutation by deletion — `source[:source.index(m)]`
                # is how you amputate a real proof and watch the arm notice.
                # An INDEX is not: `corpus["a.py"]` is a lookup, not a plant.
                bounds = {
                    self.origin(part)
                    for part in (node.slice.lower, node.slice.upper, node.slice.step)
                    if part is not None
                }
                if LITERAL in bounds or MUTATED in bounds:
                    return MUTATED
            return base
        if isinstance(node, ast.Starred | ast.Attribute):
            return self.origin(node.value)
        if isinstance(node, ast.IfExp):
            body, other = self.origin(node.body), self.origin(node.orelse)
            return body if body == other else UNKNOWN
        if isinstance(node, ast.ListComp | ast.SetComp | ast.GeneratorExp):
            return self._combine(
                {self.origin(g.iter) for g in node.generators}, empty=UNKNOWN
            )
        if isinstance(node, ast.DictComp):
            return self._combine(
                {self.origin(g.iter) for g in node.generators}, empty=UNKNOWN
            )
        if isinstance(node, ast.Call):
            return self._call_origin(node)
        return UNKNOWN

    @staticmethod
    def _combine(origins: set[str], *, empty: str) -> str:
        if not origins:
            return empty
        if MUTATED in origins:
            return MUTATED
        if CORPUS in origins:
            return CORPUS
        return LITERAL if origins == {LITERAL} else UNKNOWN

    def _call_origin(self, node: ast.Call) -> str:
        func = node.func
        if isinstance(func, ast.Attribute):
            if func.attr in FS_READ:
                return CORPUS
            base = self.origin(func.value)
            if func.attr in MATCHER_CALLS and node.args:
                return self.origin(node.args[0])
            if func.attr in LOCATOR_CALLS and node.args:
                # `source.index(marker)` is an author-chosen offset INTO real
                # bytes; it carries the fixture, not the corpus.
                return self.origin(node.args[0])
            if func.attr in SURGERY_CALLS and base in (CORPUS, MUTATED):
                if LITERAL in {self.origin(a) for a in node.args}:
                    return MUTATED
            return base
        if isinstance(func, ast.Name):
            if func.id in self.scanners:
                return CORPUS
            if func.id == "Path":
                return CORPUS
            if func.id in self.helpers:
                args = {self.origin(a) for a in node.args}
                return UNKNOWN if not args else self._combine(args, empty=UNKNOWN)
            if func.id in {
                "dict",
                "list",
                "set",
                "sorted",
                "tuple",
                "frozenset",
                "Counter",
                "len",
            }:
                return self.origin(node.args[0]) if node.args else LITERAL
        return UNKNOWN

    def _bind(self, target: ast.expr, origin: str) -> None:
        if isinstance(target, ast.Name):
            previous = self.env.get(target.id)
            if previous in (CORPUS, MUTATED) and origin == LITERAL:
                # Rebinding a corpus name with a fixture is a plant, not a reset.
                self.env[target.id] = MUTATED
            else:
                self.env[target.id] = origin
        elif isinstance(target, ast.Tuple | ast.List):
            for element in target.elts:
                self._bind(element, origin)
        elif isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name):
            # `mutated["web.py"] = planted + mutated["web.py"]`
            if self.env.get(target.value.id) in (CORPUS, MUTATED):
                self.env[target.value.id] = MUTATED

    def _record(self, node: ast.Call) -> None:
        func = node.func
        name: str | None = None
        if (
            isinstance(func, ast.Name)
            and func.id in self.helpers
            and func.id not in self.selectors
        ):
            # A selector spends its argument on a PATH, so the literal names a
            # place rather than planting bytes — see `_is_selector`.
            name = func.id
        elif isinstance(func, ast.Attribute) and func.attr in MATCHER_CALLS:
            base = func.value
            if isinstance(base, ast.Name) and base.id in self.constants:
                name = f"{base.id}.{func.attr}"
        if name is not None and node.args:
            self.calls.append((name, tuple(self.origin(a) for a in node.args)))

    def _visit(self, node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Assign):
                origin = self.origin(child.value)
                for target in child.targets:
                    self._bind(target, origin)
            elif isinstance(child, ast.AnnAssign) and child.value is not None:
                self._bind(child.target, self.origin(child.value))
            elif isinstance(child, ast.AugAssign):
                self._bind(child.target, self.origin(child.value))
            elif isinstance(child, ast.For | ast.AsyncFor):
                self._bind(child.target, self.origin(child.iter))
            if isinstance(child, ast.Call):
                self._record(child)
            self._visit(child)


# ---------------------------------------------------------------------------
# The three arms
# ---------------------------------------------------------------------------


def _module_constants(tree: ast.Module) -> set[str]:
    return {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }


def arm_1_fixture_only(source: str) -> dict[str, list[str]]:
    """ARM 1 — detectors with fixture proofs and no in-situ liveness leg."""
    tree = ast.parse(source)
    roots = module_roots(tree)
    helpers = helper_functions(tree)
    scanners = {n for n, fn in helpers.items() if _reads_corpus(fn, roots, helpers)}
    selectors = {n for n, fn in helpers.items() if _is_selector(fn, roots)}
    constants = _module_constants(tree)
    graph = _call_graph(helpers)

    fixture: dict[str, set[str]] = {}
    live: set[str] = set()
    for node in tree.body:
        if not (isinstance(node, ast.FunctionDef) and node.name.startswith("test_")):
            continue
        origins = _Origins(node, roots, helpers, scanners, selectors, constants)
        for detector, args in origins.calls:
            if MUTATED in args:
                live.add(detector)
            elif LITERAL in args:
                fixture.setdefault(detector, set()).add(node.name)

    # Liveness is transitive: a helper called by a mutation-driven detector was
    # itself driven over the mutated real bytes.
    frontier = list(live)
    while frontier:
        for callee in graph.get(frontier.pop(), set()):
            if callee not in live:
                live.add(callee)
                frontier.append(callee)

    return {
        detector: sorted(proofs)
        for detector, proofs in sorted(fixture.items())
        if detector not in live
    }


def arm_2_no_firing_proof(source: str) -> list[str]:
    """ARM 2 — a guard that scans the tree and never plants anything.

    Returns the file's discovery handles when it qualifies, so the backlog
    names what a proof would have to be written against. An empty list means
    the guard is NOT in the population.
    """
    tree = ast.parse(source)
    roots = module_roots(tree)
    helpers = helper_functions(tree)
    if not roots:
        return []
    scanners = {n for n, fn in helpers.items() if _reads_corpus(fn, roots, helpers)}
    reads_anywhere = scanners or any(
        isinstance(node, ast.Attribute) and node.attr in FS_READ
        for node in ast.walk(tree)
    )
    if not reads_anywhere:
        return []

    selectors = {n for n, fn in helpers.items() if _is_selector(fn, roots)}
    constants = _module_constants(tree)
    for node in tree.body:
        if not (isinstance(node, ast.FunctionDef) and node.name.startswith("test_")):
            continue
        origins = _Origins(node, roots, helpers, scanners, selectors, constants)
        for _detector, args in origins.calls:
            if LITERAL in args or MUTATED in args:
                return []  # something, somewhere, plants a violation

    handles = sorted(scanners) or ["<inline>"]
    return handles


def arm_3_vacuous_discovery(source: str) -> list[str]:
    """ARM 3 — real-tree discovery whose emptiness nobody would notice."""
    tree = ast.parse(source)
    names = path_names(tree)
    if not names:
        return []
    aliases = _alias_closure(tree)
    proved = set(_nonempty_assertion_names(tree))
    for name in list(proved):
        proved |= aliases.get(name, set())
    # A proved helper proves the discovery it composes: asserting
    # `_admin_and_auth_templates()` is non-empty proves the `_glob_all` inside
    # it fired. Without this hop the arm reports guarded sites, becomes noise,
    # and gets switched off — the slower route to an unmonitored region.
    graph = _call_graph(helper_functions(tree))
    frontier = [name for name in proved if name in graph]
    while frontier:
        for callee in graph.get(frontier.pop(), set()):
            if callee not in proved:
                proved.add(callee)
                frontier.append(callee)

    enclosing: dict[int, str] = {}
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef):
            for sub in ast.walk(fn):
                enclosing.setdefault(id(sub), fn.name)

    vacuous: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr not in DISCOVERY_CALLS:
            continue
        if not (_names_in(node.func.value) & names):
            continue
        scope = enclosing.get(id(node), "<module>")
        handles = {scope}
        for assignment in ast.walk(tree):
            if isinstance(assignment, ast.Assign) and any(
                sub is node for sub in ast.walk(assignment.value)
            ):
                for target in assignment.targets:
                    handles |= _bound_names(target)
        if handles & proved:
            continue
        pattern = (
            node.args[0].value
            if node.args and isinstance(node.args[0], ast.Constant)
            else ""
        )
        vacuous.append(f"{scope}::{node.func.attr}({pattern})")
    return sorted(set(vacuous))


def _names_in(node: ast.AST) -> set[str]:
    return {sub.id for sub in ast.walk(node) if isinstance(sub, ast.Name)}


def _alias_closure(tree: ast.Module) -> dict[str, set[str]]:
    aliases: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        sources = _names_in(node.value) | {
            sub.func.id
            for sub in ast.walk(node.value)
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
        }
        for target in node.targets:
            for name in _bound_names(target):
                aliases.setdefault(name, set()).update(sources)
    for _ in range(6):
        for values in aliases.values():
            for name in list(values):
                values |= aliases.get(name, set())
    return aliases


def _nonempty_assertion_names(tree: ast.Module) -> set[str]:
    """Names mentioned by an assertion that would FAIL on an empty result."""
    proved: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assert):
            continue
        test = node.test
        holds = False
        if isinstance(test, ast.Name | ast.Call | ast.Subscript | ast.Attribute):
            holds = True  # bare truthiness: `assert roots, "..."`
        elif isinstance(test, ast.BoolOp):
            holds = True
        elif isinstance(test, ast.Compare) and len(test.ops) == 1:
            operator, right = test.ops[0], test.comparators[0]
            if isinstance(operator, ast.Gt | ast.GtE) and isinstance(
                right, ast.Constant
            ):
                holds = isinstance(right.value, int) and right.value >= 1
            elif isinstance(operator, ast.Eq):
                # Equality fails on an empty discovery UNLESS the expected side
                # is itself empty. `== []` and `== set()` are the shapes that
                # pass vacuously; everything else — a non-empty literal, or a
                # set built independently of the scan (`on_disk == registered`)
                # — is a real non-emptiness proof.
                if isinstance(right, ast.Set | ast.List | ast.Tuple):
                    holds = len(right.elts) >= 1
                elif isinstance(right, ast.Dict):
                    holds = len(right.keys) >= 1
                elif isinstance(right, ast.Constant):
                    holds = isinstance(right.value, int) and right.value >= 1
                elif isinstance(right, ast.Call) and isinstance(right.func, ast.Name):
                    holds = right.func.id not in {"set", "list", "dict", "tuple"} or (
                        bool(right.args)
                    )
                elif isinstance(right, ast.Name | ast.Attribute | ast.Subscript):
                    holds = True
            elif isinstance(operator, ast.In | ast.NotIn | ast.NotEq):
                holds = True
        if holds:
            proved |= _names_in(test)
            proved |= {
                sub.func.id
                for sub in ast.walk(test)
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
            }
    return proved


# ---------------------------------------------------------------------------
# Discovery and the whole-suite scan
# ---------------------------------------------------------------------------


def guard_sources() -> dict[str, str]:
    """The real guard suite, keyed by repo-relative path.

    Every `tests/architecture/test_*.py`, this file included. Returning bytes
    rather than paths is what lets the liveness legs mutate the real source in
    memory and leave the tree untouched.
    """
    return {
        path.relative_to(PROJECT_ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(GUARD_DIR.glob("test_*.py"))
    }


def scan(sources: dict[str, str]) -> dict[str, dict[str, list[str]]]:
    fixture_only: dict[str, list[str]] = {}
    unproved: dict[str, list[str]] = {}
    vacuous: dict[str, list[str]] = {}
    for path, source in sorted(sources.items()):
        for detector, proofs in arm_1_fixture_only(source).items():
            fixture_only[f"{path}::{detector}"] = proofs
        handles = arm_2_no_firing_proof(source)
        if handles:
            unproved[path] = handles
        for site in arm_3_vacuous_discovery(source):
            vacuous[f"{path}::{site}"] = []
    return {
        "fixture_only_arms": fixture_only,
        "guards_without_a_firing_proof": unproved,
        "vacuous_discovery_scopes": vacuous,
    }


POPULATIONS: Final[tuple[str, ...]] = (
    "fixture_only_arms",
    "guards_without_a_firing_proof",
    "vacuous_discovery_scopes",
)


def load_baseline() -> dict[str, object]:
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def describe_drift(
    live: dict[str, dict[str, list[str]]], baseline: dict[str, dict[str, list[str]]]
) -> list[str]:
    problems: list[str] = []
    for population in POPULATIONS:
        now = live.get(population, {})
        was = baseline.get(population, {})
        for key in sorted(set(now) | set(was)):
            if key not in was:
                problems.append(f"ADDED   {population}: {key}")
            elif key not in now:
                problems.append(f"REMOVED {population}: {key}")
            elif now[key] != was[key]:
                problems.append(f"CHANGED {population}: {key} {was[key]} -> {now[key]}")
    return problems


def totals(scanned: dict[str, dict[str, list[str]]]) -> dict[str, int]:
    return {population: len(scanned.get(population, {})) for population in POPULATIONS}


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def test_the_guard_corpus_is_discovered_and_not_vacuous() -> None:
    """This ratchet's own discovery is the first thing that could go silent."""
    sources = guard_sources()
    assert len(sources) >= 40, (
        f"only {len(sources)} guard modules discovered under {GUARD_DIR}; "
        "the ratchet's own scan looks broken"
    )
    assert "tests/architecture/test_guard_proof_ratchet.py" in sources, (
        "the ratchet must be inside its own corpus — a guard suite that "
        "exempts its auditor is exactly the unmonitored region ADR-0018 bans"
    )
    assert "tests/architecture/test_presentation_boundary.py" in sources


def test_the_backlog_matches_its_baseline_exactly() -> None:
    """TWO-DIRECTIONAL. Fails when a population grows AND when it shrinks."""
    baseline = load_baseline()
    problems = describe_drift(scan(guard_sources()), baseline["populations"])  # type: ignore[arg-type]
    assert not problems, (
        "the guard-proof backlog drifted from its frozen baseline.\n"
        + "\n".join(f"  {line}" for line in problems)
        + "\n\nADDED means a new arm shipped without the leg ADR-0018 requires "
        "— add the leg rather than the baseline entry.\nREMOVED/CHANGED means "
        "a retrofit (or a deletion, or a rename, or a glob that stopped "
        "matching). All four must be reviewable, so record it:\n"
        "  make guard-proof-baseline"
    )


def test_the_baseline_totals_agree_with_its_own_entries() -> None:
    """A headline that is not derived from the entries can be edited alone."""
    baseline = load_baseline()
    recorded: dict[str, dict[str, list[str]]] = baseline["populations"]  # type: ignore[assignment]
    assert baseline["totals"] == {p: len(recorded.get(p, {})) for p in POPULATIONS}, (
        "baseline 'totals' disagrees with its own populations; regenerate with "
        "`make guard-proof-baseline` rather than hand-editing"
    )


def test_the_ratchet_fails_in_both_directions() -> None:
    """The two-directional claim, proved against the REAL frozen baseline.

    A ratchet that only catches growth lets a retrofit — or a deletion, or a
    rename, or a glob that stopped matching — pass as silence. All four look
    identical from the outside, so all four must land as a diff. Each
    population is exercised separately: one direction proved on one population
    would say nothing about the other five combinations.
    """
    baseline: dict[str, dict[str, list[str]]] = load_baseline()["populations"]  # type: ignore[assignment]
    for population in POPULATIONS:
        entries = baseline[population]
        assert entries, f"{population} is empty; this proof would be vacuous"

        grown = {p: dict(v) for p, v in baseline.items()}
        grown[population]["tests/architecture/test_planted_guard.py::_planted"] = []
        problems = describe_drift(grown, baseline)
        assert problems and problems[0].startswith("ADDED"), (population, problems)

        shrunk = {p: dict(v) for p, v in baseline.items()}
        retired = next(iter(shrunk[population]))
        del shrunk[population][retired]
        problems = describe_drift(shrunk, baseline)
        assert problems and problems[0].startswith("REMOVED"), (population, problems)

    # And a changed value — a retrofit that adds one proof without adding the
    # leg — is neither ADDED nor REMOVED, so it needs its own direction.
    changed = {p: dict(v) for p, v in baseline.items()}
    key = next(iter(changed["fixture_only_arms"]))
    changed["fixture_only_arms"][key] = [*changed["fixture_only_arms"][key], "test_new"]
    problems = describe_drift(changed, baseline)
    assert problems and problems[0].startswith("CHANGED"), problems


def test_the_worked_example_is_not_in_any_population() -> None:
    """ADR-0018 names one three-leg guard. It must keep all three.

    Anchored against the LIVE scan, not the baseline, so a regenerated
    baseline cannot legalise the worked example rotting.
    """
    live = scan(guard_sources())
    example = "tests/architecture/test_presentation_boundary.py"
    offenders = [
        f"{population}: {key}"
        for population in POPULATIONS
        for key in live[population]
        if key.startswith(example)
    ]
    assert not offenders, (
        "ADR-0018's worked example lost a leg — the ADR points readers here: "
        f"{offenders}"
    )


def test_this_ratchet_holds_itself_to_its_own_standard() -> None:
    """The auditor is inside the corpus and must be clean of all three arms."""
    live = scan(guard_sources())
    me = "tests/architecture/test_guard_proof_ratchet.py"
    offenders = [
        f"{population}: {key}"
        for population in POPULATIONS
        for key in live[population]
        if key.startswith(me)
    ]
    assert not offenders, f"the guard-proof ratchet fails its own gate: {offenders}"


# ---------------------------------------------------------------------------
# ARM 1 — fixture-only detectors. Three legs.
# ---------------------------------------------------------------------------


def test_arm_1_leg_1_sensitivity_a_two_leg_detector_is_reported() -> None:
    """SENSITIVITY. A detector driven only by fixtures fires arm 1."""
    guard = (
        "from pathlib import Path\n"
        "ROOT = Path(__file__).resolve().parents[2]\n"
        "def _classify(text):\n"
        "    return 'bad' in text\n"
        "def _sources():\n"
        "    return [p.read_text() for p in ROOT.glob('*.py')]\n"
        "def test_nothing_is_bad():\n"
        "    assert not [s for s in _sources() if _classify(s)]\n"
        "def test_sensitivity():\n"
        "    assert _classify('bad')\n"
    )
    assert arm_1_fixture_only(guard) == {"_classify": ["test_sensitivity"]}


def test_arm_1_leg_2_specificity_an_in_situ_proof_earns_the_leg() -> None:
    """SPECIFICITY. The shapes arm 1 was narrowed against stay silent.

    Three near-misses, each a real pattern in this suite:

    * the SAME guard given an in-situ mutation proof — the retrofit target, and
      the arm must go quiet the moment it lands, or the programme can never
      finish;
    * a literal handed to a SCANNER — `_entry("dotmac-files")` is a selector
      into the real tree, not a planted violation, and counting it would fill
      the backlog with noise until the gate got switched off;
    * a helper driven transitively by a mutation proof, which is exercised over
      real bytes even though nothing calls it directly.
    """
    retrofitted = (
        "from pathlib import Path\n"
        "ROOT = Path(__file__).resolve().parents[2]\n"
        "def _classify(text):\n"
        "    return 'bad' in text\n"
        "def _sources():\n"
        "    return ROOT.glob('*.py')\n"
        "def test_sensitivity():\n"
        "    assert _classify('bad')\n"
        "def test_liveness():\n"
        "    real = _sources()[0].read_text()\n"
        "    assert _classify(real + 'bad')\n"
    )
    assert arm_1_fixture_only(retrofitted) == {}

    selector = (
        "from pathlib import Path\n"
        "ROOT = Path(__file__).resolve().parents[2]\n"
        "def _entry(name):\n"
        "    return (ROOT / name / 'pyproject.toml').read_text()\n"
        "def test_files_is_allowlisted():\n"
        "    assert 'mod_files' in _entry('dotmac-files')\n"
    )
    assert arm_1_fixture_only(selector) == {}

    transitive = (
        "from pathlib import Path\n"
        "ROOT = Path(__file__).resolve().parents[2]\n"
        "def _tokenise(text):\n"
        "    return text.split()\n"
        "def _classify(text):\n"
        "    return 'bad' in _tokenise(text)\n"
        "def _sources():\n"
        "    return ROOT.glob('*.py')\n"
        "def test_sensitivity():\n"
        "    assert _tokenise('bad')\n"
        "def test_liveness():\n"
        "    real = _sources()[0].read_text()\n"
        "    assert _classify(real + 'bad')\n"
    )
    assert arm_1_fixture_only(transitive) == {}


def test_arm_1_leg_3_liveness_it_classifies_real_guard_source() -> None:
    """LIVENESS. Real discovery, real bytes, real classifier — both ways.

    Positive direction: a NAMED real guard in this suite is correctly
    classified as fixture-only, so the arm is demonstrably not inert.

    Negative direction: ADR-0018's worked example is clean today, and stays
    clean only because of one in-situ mutation call inside
    `test_leg_3_liveness_...`. Cut that test off the REAL source and the arm
    must report the example's detector — which proves the arm is measuring the
    liveness leg itself, not merely counting tests whose names it likes.
    """
    corpus = guard_sources()

    web = "tests/architecture/test_web_conventions.py"
    assert web in corpus, "discovery no longer reaches the real web-conventions guard"
    assert "_template_offenders" in arm_1_fixture_only(corpus[web]), (
        "arm 1 did not classify a REAL two-leg detector in this repository — "
        "it is inert against the shapes this guard suite actually uses "
        "(ADR-0018 leg 3)"
    )

    example = "tests/architecture/test_presentation_boundary.py"
    assert example in corpus
    source = corpus[example]
    assert arm_1_fixture_only(source) == {}, "the worked example must start clean"

    marker = "def test_leg_3_liveness_the_arm_classifies_real_presentation_source"
    assert marker in source, "the worked example's liveness leg was renamed"
    amputated = source[: source.index(marker)]

    assert "_authority_crossings" in arm_1_fixture_only(amputated), (
        "removing the ONLY in-situ mutation proof from the real worked example "
        "did not make its detector fixture-only — arm 1 is not measuring what "
        "it claims to (ADR-0018 leg 3)"
    )
    assert arm_1_fixture_only(corpus[example]) == {}, "the corpus was disturbed"


# ---------------------------------------------------------------------------
# ARM 2 — guards with no firing proof at all. Three legs.
# ---------------------------------------------------------------------------


def test_arm_2_leg_1_sensitivity_a_guard_that_never_plants_is_reported() -> None:
    """SENSITIVITY. A scanning guard with no planted violation anywhere."""
    guard = (
        "from pathlib import Path\n"
        "ROOT = Path(__file__).resolve().parents[2]\n"
        "def _sources():\n"
        "    return sorted(ROOT.glob('*.py'))\n"
        "def test_no_source_is_bad():\n"
        "    assert _sources()\n"
        "    assert not [p for p in _sources() if 'bad' in p.read_text()]\n"
    )
    assert arm_2_no_firing_proof(guard) == ["_sources"]


def test_arm_2_leg_2_specificity_a_planting_or_non_scanning_guard_is_silent() -> None:
    """SPECIFICITY. Two near-misses arm 2 must not collect.

    A guard that DOES plant is out of the population regardless of how thin
    the proof is — arm 2 counts absence, and arm 1 is what judges quality. And
    a guard that never touches the tree (an import-and-assert registry test)
    has nothing to be blind about; sweeping those in would bury the real
    backlog under every unit-shaped test in the directory.
    """
    plants = (
        "from pathlib import Path\n"
        "ROOT = Path(__file__).resolve().parents[2]\n"
        "def _classify(text):\n"
        "    return 'bad' in text\n"
        "def _sources():\n"
        "    return sorted(ROOT.glob('*.py'))\n"
        "def test_clean():\n"
        "    assert _sources()\n"
        "def test_sensitivity():\n"
        "    assert _classify('bad')\n"
    )
    assert arm_2_no_firing_proof(plants) == []

    no_corpus = (
        "from app.features import FEATURE_MODULES\n"
        "def test_every_module_is_registered():\n"
        "    assert len(FEATURE_MODULES) == 8\n"
    )
    assert arm_2_no_firing_proof(no_corpus) == []


def test_arm_2_leg_3_liveness_it_classifies_real_guard_source() -> None:
    """LIVENESS. Real bytes, both directions.

    Positive: `test_thin_wrappers.py` is a real, live, load-bearing guard —
    it scans every `router.py` in the tree — and nothing in it has ever
    planted a violation. Arm 2 must say so by name.

    Negative: the palette ratchet is a real guard that DOES plant, so it is
    out. Strip its proofs from the REAL source and it must fall in — which is
    the check that arm 2 keys on planting rather than on file size, helper
    count, or a naming convention.
    """
    corpus = guard_sources()

    thin = "tests/architecture/test_thin_wrappers.py"
    assert thin in corpus, "discovery no longer reaches the real thin-wrappers guard"
    assert arm_2_no_firing_proof(corpus[thin]) == ["_router_files"], (
        "arm 2 did not classify a REAL unproved guard in this repository "
        "(ADR-0018 leg 3)"
    )

    palette = "tests/architecture/test_palette_ratchet.py"
    assert palette in corpus
    source = corpus[palette]
    assert arm_2_no_firing_proof(source) == [], "the palette ratchet does plant"

    marker = "def test_sensitivity_a_new_palette_utility_is_detected"
    assert marker in source, "the palette ratchet's first sensitivity proof was renamed"
    amputated = source[: source.index(marker)]

    assert arm_2_no_firing_proof(amputated), (
        "removing every planted violation from the REAL palette ratchet did "
        "not put it in the no-firing-proof population — arm 2 is inert "
        "(ADR-0018 leg 3)"
    )
    assert arm_2_no_firing_proof(corpus[palette]) == [], "the corpus was disturbed"


# ---------------------------------------------------------------------------
# ARM 3 — vacuous discovery scopes. Three legs.
# ---------------------------------------------------------------------------


def test_arm_3_leg_1_sensitivity_an_unasserted_scope_is_reported() -> None:
    """SENSITIVITY. Discovery nobody asserted is reported, loop form included."""
    direct = (
        "from pathlib import Path\n"
        "ROOT = Path(__file__).resolve().parents[2]\n"
        "def test_no_router_queries():\n"
        "    for path in ROOT.rglob('*.py'):\n"
        "        assert 'db.query' not in path.read_text()\n"
    )
    assert arm_3_vacuous_discovery(direct) == ["test_no_router_queries::rglob(*.py)"]

    through_a_loop_variable = (
        "from pathlib import Path\n"
        "ROOT = Path(__file__).resolve().parents[2]\n"
        "ROOTS = (ROOT / 'app', ROOT / 'packages')\n"
        "def _files():\n"
        "    return [p for root in ROOTS for p in root.rglob('*.py')]\n"
        "def test_clean():\n"
        "    assert not [p for p in _files() if 'bad' in p.read_text()]\n"
    )
    assert arm_3_vacuous_discovery(through_a_loop_variable) == ["_files::rglob(*.py)"]


def test_arm_3_leg_2_specificity_an_asserted_or_non_corpus_scope_is_silent() -> None:
    """SPECIFICITY. Three near-misses.

    An asserted scope is the whole point of the arm and must go quiet. A
    `>= N` floor counts as an assertion just as a bare truthiness check does —
    `test_thin_wrappers.py` uses the first form and `test_palette_ratchet.py`
    uses both, and an arm that only understood one spelling would report a
    guarded site and be switched off. A glob over a `tmp_path` is not
    real-tree discovery at all: nothing about it can silently stop matching.
    """
    asserted = (
        "from pathlib import Path\n"
        "ROOT = Path(__file__).resolve().parents[2]\n"
        "def _files():\n"
        "    return sorted(ROOT.rglob('*.py'))\n"
        "def test_scan_is_not_vacuous():\n"
        "    assert _files()\n"
    )
    assert arm_3_vacuous_discovery(asserted) == []

    floored = (
        "from pathlib import Path\n"
        "ROOT = Path(__file__).resolve().parents[2]\n"
        "def _files():\n"
        "    return sorted(ROOT.rglob('*.py'))\n"
        "def test_scan_is_not_vacuous():\n"
        "    assert len(_files()) >= 5\n"
    )
    assert arm_3_vacuous_discovery(floored) == []

    temporary = (
        "from pathlib import Path\n"
        "ROOT = Path(__file__).resolve().parents[2]\n"
        "def test_writes_a_fixture(tmp_path):\n"
        "    (tmp_path / 'a.py').write_text('x')\n"
        "    assert not list(tmp_path.rglob('*.txt'))\n"
    )
    assert arm_3_vacuous_discovery(temporary) == []


def test_arm_3_leg_3_liveness_it_classifies_real_guard_source() -> None:
    """LIVENESS. Real bytes, both directions.

    Positive: `test_no_feature_rollback.py::_feature_py_files` really does
    `rglob("*.py")` over `app/features` with nothing asserting the result is
    non-empty. Rename that directory and the guard passes over zero files.

    Negative: `test_thin_wrappers.py` scans the same way and DOES assert. Cut
    its one non-vacuity assertion out of the REAL source and its site must
    turn vacuous — proving the arm reads the assertion rather than the
    directory.
    """
    corpus = guard_sources()

    rollback = "tests/architecture/test_no_feature_rollback.py"
    assert rollback in corpus, "discovery no longer reaches the real rollback guard"
    assert arm_3_vacuous_discovery(corpus[rollback]) == [
        "_feature_py_files::rglob(*.py)"
    ], "arm 3 did not classify a REAL vacuous scope in this repository (leg 3)"

    thin = "tests/architecture/test_thin_wrappers.py"
    assert thin in corpus
    source = corpus[thin]
    assert arm_3_vacuous_discovery(source) == [], "thin-wrappers asserts its scan"

    proof = "def test_router_scan_is_not_vacuous"
    survivor = "def test_routers_do_not_issue_direct_queries"
    assert proof in source, "the thin-wrappers non-vacuity proof was renamed"
    assert survivor in source, "the thin-wrappers production check was renamed"
    amputated = source[: source.index(proof)] + source[source.index(survivor) :]
    assert "rglob" in amputated, "the amputation removed the discovery itself"

    assert arm_3_vacuous_discovery(amputated) == ["_router_files::rglob(*.py)"], (
        "removing the ONLY non-vacuity assertion from the REAL thin-wrappers "
        "guard did not make its scope vacuous — arm 3 is inert (leg 3)"
    )
    assert arm_3_vacuous_discovery(corpus[thin]) == [], "the corpus was disturbed"
