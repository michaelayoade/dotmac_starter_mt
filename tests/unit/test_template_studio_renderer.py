"""Parity proof for the ported Sub renderer contract (ADR-0006 § 5b).

These are `dotmac_sub:tests/test_notification_template_renderer.py`'s seven tests,
ported. The 2026-08-10 source audit selected Sub's renderer as the qualifying
implementation, and the product-first amendment requires its behaviour tests to
come across with the code — a port whose proof stayed behind is a rewrite wearing
a port's name.

What changed in the port, and why each change is faithful rather than a
weakening:

- **The vocabulary is registered, not hardcoded.** Sub asserts against its own
  `KNOWN_PLACEHOLDERS` (`subscriber_name`, `invoice_number`, …). A
  product-neutral module cannot own an ISP's words, so these tests register a
  `RenderContext` and assert the same behaviours against it. The MECHANISM under
  test — single braces render, double braces are rejected at save time, unknown
  names are rejected at save time, unknown names survive literally at render
  time — is unchanged.
- **Context-awareness is a column, not a code lookup.** Sub decides a template's
  allowed variables by looking its `code` up in the event-spec registry
  (`allowed_variables_for_code`). Here the template names its context
  explicitly. Same guarantee: a template is validated against the variables the
  send path that will render it can actually supply, and never against a union.
- **`validate_template_text` raises `BadRequestError`, not `ValueError`.** The
  kernel's typed exception, so the API surfaces a 400 rather than a 500.

The two tests below marked `# NEW` have no Sub counterpart; they cover the
registry seam the port introduced.
"""

from __future__ import annotations

import pytest
from dotmac_kernel.exceptions import BadRequestError
from dotmac_template_studio import service
from dotmac_template_studio.contexts import (
    RenderContext,
    RenderContextConflictError,
    UnknownRenderContextError,
    _reset_for_tests,
    get_context,
    register_contexts,
    registered_contexts,
)

BILLING = RenderContext(
    name="billing",
    variables=("customer_name", "invoice_number", "amount", "due_date"),
    description="What the billing send path supplies.",
)
OUTAGE = RenderContext(
    name="outage",
    variables=("customer_name", "location", "grace_hours"),
    description="What the outage send path supplies.",
)


@pytest.fixture(autouse=True)
def _isolated_registry():
    """The registry is process-global (an import-time declaration, like
    `register_specs`), so a test that registers a context would otherwise leak it
    into every later test in the same process."""
    previous = _reset_for_tests([BILLING, OUTAGE])
    yield
    _reset_for_tests(previous.values())


# ── The seven ported behaviours ─────────────────────────────────────────────


def test_render_single_brace_contract() -> None:
    """Sub: `test_render_template_text_single_brace_contract`."""
    rendered = service.render(
        "Hello {customer_name}, invoice {invoice_number} is due {due_date}.",
        {
            "customer_name": "Jane Doe",
            "invoice_number": "INV-2026-0001",
            "due_date": "Mar 01, 2026",
        },
    )
    assert rendered == "Hello Jane Doe, invoice INV-2026-0001 is due Mar 01, 2026."


def test_render_does_not_substitute_double_braces() -> None:
    """Sub: `test_render_template_text_does_not_substitute_double_braces`.

    The regression that gave the contract its shape: the live renderer fills
    single braces only, so a double-brace token reached customers literally.
    Rendering must NOT quietly fix it — that would hide the defect that
    save-time validation exists to reject.
    """
    rendered = service.render(
        "Amount due: {{amount}}", {"amount": "N12,500.00"}, strict=False
    )
    assert rendered == "Amount due: {{amount}}"


def test_render_keeps_unknown_variables_when_not_strict() -> None:
    """Sub: `test_render_template_text_keeps_unknown_variables`.

    Unknown names are left visible rather than blanked, so a mistake shows up as
    a mistake. This is the preview path; the strict path below is the send path.
    """
    rendered = service.render(
        "Hello {customer_name}, ref {mystery}.", {"customer_name": "Jane"}, strict=False
    )
    assert rendered == "Hello Jane, ref {mystery}."


def test_render_strict_raises_on_a_missing_value() -> None:
    """Sub's live path SUPPRESSES a send whose placeholders did not all resolve.

    This module does not send, so the faithful equivalent is refusing to return
    the half-rendered body at all — the caller never receives something it might
    go on to deliver.
    """
    with pytest.raises(BadRequestError, match="mystery"):
        service.render("Hello {customer_name}, ref {mystery}.", {"customer_name": "J"})


def test_validate_rejects_double_brace_and_unknown_names() -> None:
    """Sub: `test_validate_template_text_rejects_double_brace_and_unknown`."""
    with pytest.raises(BadRequestError) as excinfo:
        service.validate_template_text(
            "Hi {{customer_name}}", "Your {nonsense} is ready", context=BILLING
        )
    message = str(excinfo.value)
    assert "double braces" in message
    assert "{nonsense}" in message
    # The author is told what they MAY use, not just what they may not.
    assert "{invoice_number}" in message


def test_validation_is_context_aware_per_send_path() -> None:
    """Sub: `test_validate_is_context_aware_for_automated_codes` +
    `..._for_bulk_codes`, collapsed — one assertion per direction.

    This is the rule that actually prevents the leak: a variable one send path
    supplies is still invalid in a template the OTHER path renders. Validating
    against the union of all contexts would pass both and reproduce the bug.
    """
    service.validate_template_text("Due {due_date}", context=BILLING)
    with pytest.raises(BadRequestError, match="due_date"):
        service.validate_template_text("Due {due_date}", context=OUTAGE)

    service.validate_template_text("Back in {grace_hours}h", context=OUTAGE)
    with pytest.raises(BadRequestError, match="grace_hours"):
        service.validate_template_text("Back in {grace_hours}h", context=BILLING)


def test_extract_variables_is_derived_from_the_content() -> None:
    """The stored variable list cannot drift from the body, and does not count a
    double-brace token as a placeholder."""
    assert service.extract_variables(
        "Hi {customer_name}, invoice {invoice_number}", subject="Re: {invoice_number}"
    ) == ["customer_name", "invoice_number"]
    assert service.extract_variables("Amount {{amount}}") == []


# ── The registry seam the port introduced ───────────────────────────────────


def test_an_unregistered_context_is_rejected_and_names_the_fix() -> None:  # NEW
    with pytest.raises(UnknownRenderContextError) as excinfo:
        get_context("nope")
    assert "register_contexts" in str(excinfo.value)
    # Known names are listed, so the error is actionable without reading source.
    assert "billing" in str(excinfo.value)


def test_re_registering_a_context_differently_is_refused() -> None:  # NEW
    """A context's variable set is a contract with every template already
    validated against it; widening or narrowing it silently would invalidate
    them with nothing failing."""
    register_contexts(BILLING)  # identical re-registration is a no-op
    assert len(registered_contexts()) == 2

    with pytest.raises(RenderContextConflictError, match="billing"):
        register_contexts(RenderContext(name="billing", variables=("something_else",)))


def test_a_context_must_declare_at_least_one_variable() -> None:  # NEW
    with pytest.raises(ValueError, match="declares no variables"):
        RenderContext(name="empty", variables=())


def test_a_context_variable_set_is_deeply_immutable() -> None:  # NEW
    """A tuple passed in becomes a frozenset — a caller cannot mutate a
    registered context's vocabulary after templates were validated against it."""
    assert isinstance(BILLING.variables, frozenset)
    with pytest.raises(AttributeError):
        BILLING.variables.add("smuggled")  # type: ignore[attr-defined]
