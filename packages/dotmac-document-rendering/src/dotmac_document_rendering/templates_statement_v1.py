"""Exact opaque profile bindings for account statements."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

from dotmac_document_rendering.contracts import (
    DocumentTemplateV1,
    TemplateInvalid,
    TemplateNotFound,
)
from dotmac_document_rendering.contracts_statement_v1 import StatementProfileBindingV1
from dotmac_document_rendering.contracts_v2 import _require_utf8_text
from dotmac_document_rendering.projection_v2 import _validate_typed_value
from dotmac_document_rendering.templates import _validate_template
from dotmac_document_rendering.templates_v2 import DEFAULT_ENGLISH_LABELS_V2

_STATEMENT_LABELS = (
    ("statement", "Statement"),
    ("statement_number", "Statement number"),
    ("summary", "Summary"),
    ("entries", "Entries"),
    ("period_start", "Period start"),
    ("period_end", "Period end"),
    ("customer_account", "Customer account"),
    ("opening_balance", "Opening balance"),
    ("total_debits", "Total debits"),
    ("total_credits", "Total credits"),
    ("closing_balance", "Closing balance"),
    ("entry_date", "Date"),
    ("entry_kind", "Type"),
    ("reference", "Reference"),
    ("debit", "Debit"),
    ("credit", "Credit"),
    ("balance", "Balance"),
)
DEFAULT_ENGLISH_LABELS_STATEMENT_V1 = DEFAULT_ENGLISH_LABELS_V2 + _STATEMENT_LABELS


@dataclass(frozen=True, slots=True)
class SelectedStatementTemplateV1:
    binding: StatementProfileBindingV1
    template: DocumentTemplateV1


class StatementTemplateCatalogV1:
    def __init__(
        self,
        *,
        templates: tuple[DocumentTemplateV1, ...],
        bindings: tuple[StatementProfileBindingV1, ...],
        assembly_file: str,
    ) -> None:
        _require_utf8_text(assembly_file, "assembly_file")
        if not assembly_file.strip():
            raise TemplateInvalid("assembly_file must name the composition fix")
        required = {key for key, _ in DEFAULT_ENGLISH_LABELS_STATEMENT_V1}
        template_index: dict[tuple[str, str], DocumentTemplateV1] = {}
        for template in templates:
            _validate_typed_value(template, DocumentTemplateV1, "template")
            _validate_template(template)
            missing = required - {label.key for label in template.labels}
            if missing:
                raise TemplateInvalid(
                    f"template is missing statement labels: {sorted(missing)}"
                )
            key = (template.template_code, template.template_version)
            if key in template_index:
                raise TemplateInvalid("duplicate template artifact")
            template_index[key] = template
        binding_index: dict[tuple[str, str, str], StatementProfileBindingV1] = {}
        for binding in bindings:
            _validate_typed_value(binding, StatementProfileBindingV1, "binding")
            if not binding.statement_profile_version.strip():
                raise TemplateInvalid(
                    "profile version must be an opaque nonempty string"
                )
            binding_key = (
                binding.statement_profile_code,
                binding.statement_profile_version,
                binding.media_type,
            )
            if (
                binding_key in binding_index
                or (binding.template_code, binding.template_version)
                not in template_index
            ):
                raise TemplateInvalid("duplicate binding or missing template artifact")
            binding_index[binding_key] = binding
        self._templates = MappingProxyType(template_index)
        self._bindings = MappingProxyType(binding_index)
        self.assembly_file = assembly_file

    def select(
        self,
        *,
        statement_profile_code: str,
        statement_profile_version: str,
        media_type: str,
    ) -> SelectedStatementTemplateV1:
        key = (statement_profile_code, statement_profile_version, media_type)
        binding = self._bindings.get(key)
        if binding is None:
            raise TemplateNotFound(
                f"no exact statement template binding for {key!r}; "
                f"declare it in {self.assembly_file}"
            )
        return SelectedStatementTemplateV1(
            binding, self._templates[(binding.template_code, binding.template_version)]
        )


__all__ = [
    "DEFAULT_ENGLISH_LABELS_STATEMENT_V1",
    "SelectedStatementTemplateV1",
    "StatementTemplateCatalogV1",
]
