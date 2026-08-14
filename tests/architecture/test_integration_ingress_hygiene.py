"""Static hygiene for the ingress path — the rules a runtime test cannot hold.

Three properties, each of which fails silently rather than loudly if it breaks:

* **No provider is named.** ADR-0024 § 7 forbids a provider enum, import list or
  `if provider == ...` branch in the control plane. A name that slips in still
  works perfectly — it just makes the module one product's, permanently.
* **No named header or query parameter is read.** The weaker, subtler form of
  the same breach: `headers.get("x-...")` is provider knowledge even when no
  forbidden WORD appears. The whole mapping crosses the boundary and the
  connector picks.
* **Nothing logs, and nothing commits.** Ingress is the one path where a raw
  body, a signature header and materialized secrets are all in scope at once,
  so a debug logger added here is a credential in a log file. And the phase
  functions must leave the transaction to the deployment's unit of work.

Every detector carries a sensitivity proof (ADR-0018): a scan that read no
files, or swallowed an exception, would look exactly like a clean pass.
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import pytest

PACKAGE = (
    Path(__file__).resolve().parents[2]
    / "packages/dotmac-integration/src/dotmac_integration"
)

#: The same list the consuming assembly uses. Matched on identifier TOKENS (see
#: `_scan_for_providers`) rather than on `\b` or on a bare substring: `meta` is a
#: substring of `metadata` and an unbounded match would fire on every
#: `importlib.metadata` import — a detector that cries wolf gets disabled, which
#: is a worse outcome than one that never fired — while `\b` is blind to
#: `stripe_api_key`, which is the form the name actually arrives in.
FORBIDDEN_PROVIDERS = (
    "whatsapp",
    "meta",
    "twilio",
    "erpnext",
    "stripe",
    "paystack",
    "flutterwave",
    "sendgrid",
    "mailgun",
)


def _sources() -> list[Path]:
    return sorted(PACKAGE.rglob("*.py"))


def _without_docstrings(source: str) -> str:
    """Blank out docstring lines, keeping comments and code line numbers.

    Docstrings are excluded because this module's prose legitimately discusses
    what a provider-shaped design would look like. Comments are NOT excluded: a
    provider name in a comment is a provider name in the module, and blanking
    them (as an `ast.unparse` round-trip would) would quietly halve the scan.
    """
    tree = ast.parse(source)
    lines = source.splitlines()
    blank: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        ):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            end = first.end_lineno or first.lineno
            blank.update(range(first.lineno, end + 1))
    return "\n".join(
        "" if number in blank else line for number, line in enumerate(lines, start=1)
    )


#: Split `camelCase` into tokens BEFORE lowering, because lowering destroys the
#: only boundary a name like `MetaAdapter` has.
_CAMEL_BOUNDARY: re.Pattern[str] = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _scan_for_providers(source: str) -> list[str]:
    """Match on IDENTIFIER TOKENS, not on `\\b`.

    `\\b` is the wrong boundary for source code: `_` and digits are word
    characters, so it never fires beside them and `stripe_api_key`,
    `provider_twilio_key` and `import whatsapp_sdk` all read as clean. Every
    realistic way a provider name enters a codebase is one of those forms, so a
    `\\b` scan looks live while being blind to the cases it exists for.

    The token boundary here is `(?<![a-z0-9])name(?![a-z0-9])`, which still
    refuses `importlib.metadata` — `meta` followed by `d` is not a token — while
    catching `meta_adapter`, and `MetaAdapter` via the camel split above.
    """
    body = _CAMEL_BOUNDARY.sub("_", _without_docstrings(source)).lower()
    return [
        name
        for name in FORBIDDEN_PROVIDERS
        if re.search(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", body)
    ]


@pytest.mark.parametrize("path", _sources(), ids=lambda p: p.name)
def test_no_provider_is_named_in_the_module_source(path: Path) -> None:
    """ADR-0024 § 7, enforced in the MODULE rather than only in the assembly.

    The control plane decides WHICH connector serves a capability; it never
    knows which company is on the other end. One provider name is all it takes
    for the next author to reach for a second.
    """
    found = _scan_for_providers(path.read_text(encoding="utf-8"))
    assert not found, f"{path.name} names provider(s) {found}"


def test_the_provider_scan_bites() -> None:
    """The ADR-0018 sensitivity proof.

    A bad path glob, an empty file list or a swallowed parse error would make
    every case above pass while scanning nothing.
    """
    assert len(_sources()) >= 15, "the provider scan found almost no files"

    synthetic = 'def handle():\n    """A docstring."""\n    return "twilio"\n'
    assert _scan_for_providers(synthetic) == ["twilio"]

    # A comment is scanned; a docstring is not.
    assert _scan_for_providers("x = 1  # stripe webhook\n") == ["stripe"]
    assert _scan_for_providers('"""stripe."""\nx = 1\n') == []

    # And the boundary really is a boundary, or every `importlib.metadata`
    # import would fire and the detector would be turned off within a week.
    assert _scan_for_providers("from importlib.metadata import entry_points\n") == []
    assert _scan_for_providers("provider = 'meta'\n") == ["meta"]
    assert _scan_for_providers("x = 1  # metadata only\n") == []

    # THE FORMS A PROVIDER NAME ACTUALLY TAKES IN CODE. A bare standalone word
    # is the one shape that almost never occurs: a name arrives as part of an
    # identifier, and `\b` — for which `_` and digits are word characters —
    # never fires beside one. Every case below passed the previous scan.
    assert _scan_for_providers("x = 'stripe_api_key'\n") == ["stripe"]
    assert _scan_for_providers("import whatsapp_sdk\n") == ["whatsapp"]
    assert _scan_for_providers("key = provider_twilio_key\n") == ["twilio"]
    assert _scan_for_providers("class MetaAdapter:\n    pass\n") == ["meta"]
    assert _scan_for_providers("client = twilioClient()\n") == ["twilio"]


def test_the_ingress_engine_reads_no_named_header_or_parameter() -> None:
    """The subtler half of the provider ban.

    `headers.get("x-hub-signature-256")` contains no forbidden WORD and is still
    a provider's wire format inside a module that may hold none. The raw
    mapping crosses the boundary untouched and the connector reads what it
    knows.
    """
    source = (PACKAGE / "ingress.py").read_text(encoding="utf-8")
    body = _without_docstrings(source)

    for accessor in ("headers.get(", "headers[", "params.get(", "params["):
        assert accessor not in body, f"ingress.py reads a named entry: {accessor}"
    assert not re.search(
        r"""["']x-[a-z0-9-]+["']""", body, re.IGNORECASE
    ), "ingress.py holds a wire-format header name"

    # Sensitivity (ADR-0018): this detector reads the file THROUGH
    # `_without_docstrings`, so a refactor of that shared helper — or a glob
    # that stopped matching — would make the assertions above scan an empty
    # string and pass for the wrong reason. The proof exercises the real helper
    # on a synthetic source, and fails if it stops returning code.
    synthetic = _without_docstrings(
        'def f(headers):\n    """Doc."""\n    return headers.get("x-signature")\n'
    )
    assert "headers.get(" in synthetic
    assert re.search(r"""["']x-[a-z0-9-]+["']""", synthetic, re.IGNORECASE)


def test_the_module_installs_no_logger() -> None:
    """Ingress is the one path holding a raw body, signature headers and
    materialized secrets at once. A debug logger here is a credential in a log
    file, and a stored diagnostic is the same thing with retention."""
    scanned = 0
    for path in _sources():
        source = path.read_text(encoding="utf-8")
        scanned += 1
        body = _without_docstrings(source)
        assert "import logging" not in body, path.name
        assert "getLogger" not in body, path.name
    assert scanned >= 15, "the logger scan read almost nothing"

    # Sensitivity (ADR-0018): `scanned >= 15` proves files were OPENED, not that
    # anything was read out of them — a `_without_docstrings` that returned ""
    # would satisfy it while asserting nothing at all.
    proof = _without_docstrings(
        '"""Doc."""\nimport logging\n\nlog = logging.getLogger(__name__)\n'
    )
    assert "import logging" in proof
    assert "getLogger" in proof


def test_the_phase_functions_never_commit_or_roll_back() -> None:
    """The transaction belongs to the deployment's unit of work.

    The module decides how MANY units of work there are and where they end —
    whole-batch atomicity is a correctness property of ingress. It does not
    decide what one IS, and taking `commit` away from the host is how a caller
    loses the ability to compose several operations into one.
    """
    body = _without_docstrings((PACKAGE / "ingress.py").read_text(encoding="utf-8"))
    assert ".commit()" not in body
    assert ".rollback()" not in body

    # Sensitivity (ADR-0018): the previous proof here was
    # `assert ".commit()" in "    db.commit()\n"` — a fact about
    # `str.__contains__` over a literal, which never touched the helper the
    # assertions above read the module through. `.rollback()` had no proof at
    # all. Both now run the REAL helper on a synthetic source that does commit.
    committing = _without_docstrings(
        'def f(db):\n    """Doc."""\n    db.commit()\n    db.rollback()\n'
    )
    assert ".commit()" in committing
    assert ".rollback()" in committing


def test_the_engine_is_module_level_functions_not_an_executable_object() -> None:
    """The consuming assembly's thinness test bans the substring `.execute(` in
    its route module, so an engine exposing `.execute()` could not be called
    from a thin adapter at all. Module-level functions keep the adapter thin
    and keep the ban meaningful."""
    from dotmac_integration import ingress

    for name in (
        "prepare_ingress",
        "verify_and_normalize",
        "record_batch",
        "challenge_response",
        "receive",
        "answer_challenge",
    ):
        attribute = getattr(ingress, name)
        assert callable(attribute)
        assert attribute.__module__ == "dotmac_integration.ingress"
        # A plain function can never carry `.execute`, so asserting its absence
        # is dead weight. What the test is actually about is that the engine is
        # a FUNCTION rather than a class an assembly would instantiate and call
        # methods on — so assert that instead.
        assert inspect.isfunction(attribute), f"{name} is not a plain function"


def test_the_ingress_surface_is_exported_from_the_top_level_namespace() -> None:
    """Submodules are not stable; the top-level namespace is.

    An assembly importing `dotmac_integration.ingress` directly would pin an
    internal path the compatibility policy does not cover.
    """
    import dotmac_integration as package

    for name in (
        "receive",
        "answer_challenge",
        "refusal_outcome",
        "IngressOutcome",
        "IngressCode",
        "PayloadTooLarge",
        "mint_ingress_endpoint",
        "rotate_ingress_endpoint",
        "revoke_ingress_endpoint",
    ):
        assert name in package.__all__, name
        assert hasattr(package, name), name
