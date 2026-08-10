"""Render contexts — the closed placeholder vocabulary a template may use.

ADR-0006 § 5b re-bases this module on Sub's proven contract. The part of that
contract that carries across products is the MECHANISM: a template's placeholders
are checked **at save time** against the exact set of variables the send path that
will render it can actually supply. The part that does not carry across is the
VOCABULARY — `subscriber_name` and `invoice_number` are an ISP's words, not a
foundation's.

So the vocabulary is registered by the product, exactly as a `SettingSpec` is
(ADR-0008: a new vocabulary is a declaration registry, never an enum). A product
declares one `RenderContext` per real send path and registers it at import time;
Template Studio owns the checking and knows none of the names.

## Why save-time validation is the load-bearing part

Sub learned this the expensive way. Its live renderer fills only the placeholders
its event context supplies, so a template authored with a variable that context
cannot produce renders literally — and a literal `{amount}` reached customers.
The fix was not a better renderer; it was refusing to SAVE a template whose
placeholders the send path cannot fill. A template that passes validation here
cannot produce a half-substituted message later, because the values are known to
exist before anyone can publish it.

That is also why a context is not merely advisory metadata: `create_template`
refuses an unregistered context name, naming this module as the fix, in the same
shape as `custom_fields`' `ENTITY_MODELS` registry.

## Registering one

    from dotmac_template_studio import RenderContext, register_contexts

    register_contexts(
        RenderContext(
            name="billing",
            variables=("customer_name", "invoice_number", "amount", "due_date"),
            description="Values the billing send path supplies.",
        )
    )

Re-registering an identical context is a no-op, so an assembly importing a module
twice is not an error. Re-registering the SAME NAME with different variables
raises: a context's variable set is a contract with every template already saved
against it, and silently widening or narrowing it would invalidate them without
anyone noticing.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from typing import Any

from dotmac_kernel.exceptions import BadRequestError

_VARIABLE_HINT = (
    "a variable name is lowercase alphanumeric with underscores, e.g. "
    "`customer_name` — it is what an author types between single braces"
)


class UnknownRenderContextError(BadRequestError):
    """A template named a context no product registered.

    A `BadRequestError` rather than a startup failure: contexts are registered at
    import time by the product, but a template naming one arrives at runtime from
    an operator, so this is bad input rather than a broken composition.
    """


class RenderContextConflictError(RuntimeError):
    """Two different variable sets were registered under one context name."""


@dataclass(frozen=True, slots=True)
class RenderContext:
    """One real send path, and the exact variables it can supply.

    Deeply immutable in the stored form: `variables` is annotated `Collection[str]`
    so a product may declare it as the tuple that reads naturally at a call site,
    but `__post_init__` normalises it to a `frozenset` before the instance is
    usable. Nothing can mutate a registered context's vocabulary afterwards, which
    matters because that vocabulary is a contract with every template already
    validated against it.
    """

    name: str
    variables: Collection[str] = field(default_factory=frozenset)
    description: str = ""

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("a render context needs a name")
        # Accept any iterable for ergonomics, store the immutable form. This is
        # the documented idiom for normalising a field on a frozen dataclass.
        object.__setattr__(self, "variables", frozenset(self.variables))
        if not self.variables:
            raise ValueError(
                f"render context {self.name!r} declares no variables — a context "
                "that can supply nothing would reject every placeholder"
            )
        for variable in self.variables:
            if not variable.replace("_", "").isalnum() or not variable.islower():
                raise ValueError(
                    f"render context {self.name!r} declares invalid variable "
                    f"{variable!r}: {_VARIABLE_HINT}"
                )

    def sorted_variables(self) -> list[str]:
        """Deterministic order, for messages and for the admin UI's hint list."""
        return sorted(self.variables)


_REGISTRY: dict[str, RenderContext] = {}


def register_contexts(*contexts: RenderContext) -> None:
    """Declare the send paths this deployment can render for.

    Idempotent for an identical re-registration; raises on a conflicting one.
    """
    for context in contexts:
        existing = _REGISTRY.get(context.name)
        if existing is not None and existing != context:
            raise RenderContextConflictError(
                f"render context {context.name!r} is already registered with a "
                f"different variable set ({existing.sorted_variables()} vs "
                f"{context.sorted_variables()}) — a context's variables are a "
                "contract with every template already validated against it"
            )
        _REGISTRY[context.name] = context


def get_context(name: str) -> RenderContext:
    """The registered context, or `UnknownRenderContextError` naming the fix."""
    context = _REGISTRY.get(name)
    if context is None:
        known = ", ".join(sorted(_REGISTRY)) or "(none registered)"
        raise UnknownRenderContextError(
            f"unknown render context {name!r} — register it with "
            f"`dotmac_template_studio.register_contexts(...)`. Known: {known}"
        )
    return context


def registered_contexts() -> tuple[RenderContext, ...]:
    """Every registered context, name-ordered. The admin UI's dropdown source."""
    return tuple(_REGISTRY[name] for name in sorted(_REGISTRY))


def _reset_for_tests(contexts: Iterable[RenderContext] | None = None) -> Any:
    """Replace the registry wholesale. Tests only — never call from product code.

    The registry is process-global (it is an import-time declaration, like
    `register_specs`), so a test that registers a context would otherwise leak it
    into every later test in the same process.
    """
    previous = dict(_REGISTRY)
    _REGISTRY.clear()
    for context in contexts or ():
        _REGISTRY[context.name] = context
    return previous


__all__ = [
    "RenderContext",
    "RenderContextConflictError",
    "UnknownRenderContextError",
    "get_context",
    "register_contexts",
    "registered_contexts",
]
