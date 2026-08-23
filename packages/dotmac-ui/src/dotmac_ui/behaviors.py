"""Published generic browser-behaviour contracts.

The JavaScript is optional, framework-light package data. It registers Alpine
factories when Alpine is present and also exports pure factories for direct use
and conformance testing. Server validation and product services remain
authoritative; these behaviours provide only interaction feedback, repeatable
field mechanics, double-submit protection and unsaved-change warnings.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class BehaviorContract:
    """One published Alpine factory and its accepted configuration keys."""

    name: str
    config_fields: tuple[str, ...]


VALIDATED_INPUT: Final[BehaviorContract] = BehaviorContract(
    name="dmuiValidatedInput",
    config_fields=("name", "initialValue", "validationUrl", "rules"),
)

FORM_SUBMIT: Final[BehaviorContract] = BehaviorContract(
    name="dmuiFormSubmit",
    config_fields=("invalidMessage",),
)

REPEATABLE_FIELDS: Final[BehaviorContract] = BehaviorContract(
    name="dmuiRepeatableFields",
    config_fields=("min", "max", "initialData", "defaultValues", "fieldPrefix"),
)

UNSAVED_CHANGES: Final[BehaviorContract] = BehaviorContract(
    name="dmuiUnsavedChanges",
    config_fields=("enabled", "message", "excludeFields", "captureDelay"),
)

BEHAVIORS: Final[tuple[BehaviorContract, ...]] = (
    VALIDATED_INPUT,
    FORM_SUBMIT,
    REPEATABLE_FIELDS,
    UNSAVED_CHANGES,
)


__all__ = [
    "BEHAVIORS",
    "FORM_SUBMIT",
    "REPEATABLE_FIELDS",
    "UNSAVED_CHANGES",
    "VALIDATED_INPUT",
    "BehaviorContract",
]
