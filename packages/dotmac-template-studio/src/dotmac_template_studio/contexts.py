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

## Context v2 (ADR-0006 § 5h, § 5i)

Everything below is additive: a context that declares only `variables` keeps its
meaning and its checks unchanged.

    RenderContext(
        name="password_reset",
        required=("reset_link",),          # every body part must use these
        url=("reset_link", "course_url"),  # URL-typed: scheme-checked in html
        optional=("note",),                # may be absent; usable via {#note}
        variables=("customer_name",),      # plain scalars (legacy field)
        lists={"courses": ("title", "course_url")},  # name -> item variables
        sample={"customer_name": "Ada", "courses": [{"title": "Intro", ...}]},
    )

`variables` after construction is the union of every scalar the context can
supply (`variables`, `required`, `optional`), so code that reads
`context.variables` keeps working. `url` marks names (scalar or item) whose
values are URLs; it does not add vocabulary by itself.

Re-registering an identical context is a no-op, so an assembly importing a module
twice is not an error. Re-registering the SAME NAME with different variables
raises: a context's variable set is a contract with every template already saved
against it, and silently widening or narrowing it would invalidate them without
anyone noticing.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from dotmac_kernel.exceptions import BadRequestError

from dotmac_template_studio.rendering import (
    HARD_MAX_LIST_ITEMS,
    MAX_LIST_ITEMS,
    RenderRefusedError,
    check_url,
)


class FrozenMap(Mapping):
    """A read-only, hashable mapping: what a frozen context stores its dicts as."""

    __slots__ = ("_data", "_hash")

    def __init__(self, data: Mapping[str, Any]) -> None:
        self._data = dict(data)
        self._hash = hash(frozenset(self._data.items()))

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __hash__(self) -> int:
        return self._hash

    def __repr__(self) -> str:
        return f"FrozenMap({self._data!r})"


_NAME = re.compile(r"^[a-z][a-z0-9_]*$")

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
    required: Collection[str] = field(default_factory=frozenset)
    optional: Collection[str] = field(default_factory=frozenset)
    url: Collection[str] = field(default_factory=frozenset)
    lists: Mapping[str, Collection[str]] = field(default_factory=dict)
    sample: Mapping[str, Any] = field(default_factory=dict)
    max_list_items: int | None = None

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("a render context needs a name")
        # Accept any iterable for ergonomics, store the immutable form. This is
        # the documented idiom for normalising a field on a frozen dataclass.
        plain = frozenset(self.variables)
        required = frozenset(self.required)
        optional = frozenset(self.optional)
        url = frozenset(self.url)
        for variable in plain:
            if not variable.replace("_", "").isalnum() or not variable.islower():
                raise ValueError(
                    f"render context {self.name!r} declares invalid variable "
                    f"{variable!r}: {_VARIABLE_HINT}"
                )
        for label, names in (("required", required), ("optional", optional)):
            for variable in names:
                self._check_name(label, variable)
        if required & optional:
            raise ValueError(
                f"render context {self.name!r} declares "
                f"{sorted(required & optional)} as both required and optional"
            )
        scalars = plain | required | optional

        lists: dict[str, frozenset[str]] = {}
        for list_name, item_vars in sorted(dict(self.lists).items()):
            self._check_name("list", list_name)
            items = frozenset(item_vars)
            if not items:
                raise ValueError(
                    f"render context {self.name!r}: list {list_name!r} declares "
                    "no item variables"
                )
            for variable in items:
                self._check_name(f"item of list {list_name!r}", variable)
            lists[list_name] = items
        clash = scalars & lists.keys()
        if clash:
            raise ValueError(
                f"render context {self.name!r}: {sorted(clash)} are declared both "
                "as a list and as a scalar variable"
            )
        if not scalars and not lists:
            raise ValueError(
                f"render context {self.name!r} declares no variables — a context "
                "that can supply nothing would reject every placeholder"
            )
        item_names = frozenset().union(*lists.values()) if lists else frozenset()
        shadowed = scalars & item_names
        if shadowed:
            raise ValueError(
                f"render context {self.name!r}: {sorted(shadowed)} are declared "
                "both as a scalar variable and as a list item variable — a list's "
                "item variables are never filled from outer values, so give them "
                "distinct names"
            )
        for variable in url:
            self._check_name("url", variable)
        stray = url - scalars - item_names
        if stray:
            raise ValueError(
                f"render context {self.name!r}: url variable(s) {sorted(stray)} "
                "are neither a declared scalar nor a list item variable"
            )
        cap = self.max_list_items
        if cap is not None and (
            isinstance(cap, bool)
            or not isinstance(cap, int)
            or not 1 <= cap <= HARD_MAX_LIST_ITEMS
        ):
            raise ValueError(
                f"render context {self.name!r}: max_list_items must be between 1 "
                f"and {HARD_MAX_LIST_ITEMS}"
            )
        object.__setattr__(self, "variables", scalars)
        object.__setattr__(self, "required", required)
        object.__setattr__(self, "optional", optional)
        object.__setattr__(self, "url", url)
        object.__setattr__(self, "lists", FrozenMap(lists))
        object.__setattr__(self, "sample", self._freeze_sample(scalars, lists, url))

    def _check_name(self, label: str, variable: str) -> None:
        if not isinstance(variable, str) or not _NAME.match(variable):
            raise ValueError(
                f"render context {self.name!r} declares invalid {label} name "
                f"{variable!r}: {_VARIABLE_HINT}"
            )

    def _freeze_sample(
        self,
        scalars: frozenset[str],
        lists: Mapping[str, frozenset[str]],
        url: frozenset[str],
    ) -> Mapping[str, Any]:
        """Validate sample values against the vocabulary and freeze them.

        A sample is what the preview screen renders with, so a sample the
        renderer would refuse is a context bug found at registration.
        """
        frozen: dict[str, Any] = {}
        bound = min(self.max_list_items or MAX_LIST_ITEMS, HARD_MAX_LIST_ITEMS)
        for key, value in dict(self.sample).items():
            if key in lists:
                if isinstance(value, str | bytes) or not isinstance(
                    value, list | tuple
                ):
                    raise ValueError(
                        f"render context {self.name!r}: sample for list {key!r} "
                        "must be a list of mappings"
                    )
                if len(value) > bound:
                    raise ValueError(
                        f"render context {self.name!r}: sample for list {key!r} "
                        f"exceeds {bound} items"
                    )
                rows = []
                for row in value:
                    if not isinstance(row, Mapping) or set(row) - lists[key]:
                        raise ValueError(
                            f"render context {self.name!r}: sample rows of list "
                            f"{key!r} must use only {sorted(lists[key])}"
                        )
                    for item_key, item_value in row.items():
                        self._check_sample_value(
                            f"{key}.{item_key}", item_key, item_value, url
                        )
                    rows.append(FrozenMap(row))
                frozen[key] = tuple(rows)
            elif key in scalars:
                self._check_sample_value(key, key, value, url)
                frozen[key] = value
            else:
                raise ValueError(
                    f"render context {self.name!r}: sample names {key!r}, which "
                    "the context does not declare"
                )
        return FrozenMap(frozen)

    def _check_sample_value(
        self, label: str, name: str, value: Any, url: frozenset[str]
    ) -> None:
        if not isinstance(value, str):
            raise ValueError(
                f"render context {self.name!r}: sample value for {label!r} must "
                "be a string"
            )
        if name in url:
            try:
                check_url(value)
            except RenderRefusedError as exc:
                raise ValueError(
                    f"render context {self.name!r}: sample for {label!r}: {exc}"
                ) from exc

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
    "FrozenMap",
    "RenderContext",
    "RenderContextConflictError",
    "UnknownRenderContextError",
    "get_context",
    "register_contexts",
    "registered_contexts",
]
