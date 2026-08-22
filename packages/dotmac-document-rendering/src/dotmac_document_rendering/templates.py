"""Immutable template artifacts and assembly-owned profile bindings."""

from __future__ import annotations

from types import MappingProxyType

from dotmac_document_rendering.contracts import (
    DocumentProfileBinding,
    DocumentTemplateV1,
    SelectedTemplateV1,
    TemplateInvalid,
    TemplateNotFound,
)

DEFAULT_ENGLISH_LABELS: tuple[tuple[str, str], ...] = (
    ("document", "Document"),
    ("invoice", "Invoice"),
    ("credit_note", "Credit note"),
    ("receipt", "Receipt"),
    ("document_number", "Document number"),
    ("issue_date", "Issue date"),
    ("due_date", "Due date"),
    ("state", "State"),
    ("seller", "Seller"),
    ("customer", "Customer"),
    ("legal_name", "Legal name"),
    ("address", "Address"),
    ("registered_identifier", "Registration"),
    ("tax_identifier", "Tax identifier"),
    ("contact", "Contact"),
    ("line_items", "Line items"),
    ("position", "#"),
    ("description", "Description"),
    ("quantity", "Quantity"),
    ("unit", "Unit"),
    ("unit_amount", "Unit amount"),
    ("line_total", "Line total"),
    ("discounts", "Discounts"),
    ("discount", "Discount"),
    ("taxes", "Taxes"),
    ("tax", "Tax"),
    ("tax_treatment", "Treatment"),
    ("tax_jurisdiction", "Jurisdiction"),
    ("tax_rates", "Rates"),
    ("taxable_basis", "Taxable basis"),
    ("tax_amount", "Tax amount"),
    ("totals", "Totals"),
    ("subtotal", "Subtotal"),
    ("tax_total", "Tax total"),
    ("total", "Total"),
    ("payment_terms", "Payment terms"),
    ("payment_instructions", "Payment instructions"),
    ("bank_name", "Bank"),
    ("account_name", "Account name"),
    ("account_number", "Account number"),
    ("sort_code", "Sort code"),
)

_REQUIRED_LABELS = frozenset(key for key, _text in DEFAULT_ENGLISH_LABELS)
_LOCALE_DEPENDENT_FORMATS = ("%a", "%A", "%b", "%B", "%c", "%x", "%X")


class TemplateCatalog:
    """Validated immutable declarations; the consuming assembly builds this."""

    __slots__ = (
        "_binding_index",
        "_template_index",
        "assembly_file",
        "bindings",
        "templates",
    )

    def __init__(
        self,
        *,
        templates: tuple[DocumentTemplateV1, ...],
        bindings: tuple[DocumentProfileBinding, ...],
        assembly_file: str,
    ) -> None:
        self.templates = tuple(templates)
        self.bindings = tuple(bindings)
        self.assembly_file = assembly_file
        if not assembly_file.strip():
            raise TemplateInvalid("assembly_file must name the composition fix")

        template_index: dict[tuple[str, str], DocumentTemplateV1] = {}
        for template in self.templates:
            _validate_template(template)
            key = (template.template_code, template.template_version)
            if key in template_index:
                raise TemplateInvalid(f"duplicate template artifact {key!r}")
            template_index[key] = template

        binding_index: dict[tuple[str, int, str], DocumentProfileBinding] = {}
        for binding in self.bindings:
            binding_key = (
                binding.document_profile_code,
                binding.document_profile_version,
                binding.media_type,
            )
            if binding_key in binding_index:
                raise TemplateInvalid(f"duplicate binding {binding_key!r}")
            template_key = (binding.template_code, binding.template_version)
            if template_key not in template_index:
                raise TemplateInvalid(
                    f"binding {binding_key!r} references missing template "
                    f"{template_key!r}"
                )
            binding_index[binding_key] = binding

        self._template_index = MappingProxyType(template_index)
        self._binding_index = MappingProxyType(binding_index)

    def select(
        self,
        *,
        document_profile_code: str,
        document_profile_version: int,
        media_type: str,
    ) -> SelectedTemplateV1:
        key = (document_profile_code, document_profile_version, media_type)
        binding = self._binding_index.get(key)
        if binding is None:
            raise TemplateNotFound(
                f"no template binding for {key!r}; declare it in {self.assembly_file}"
            )
        template = self._template_index[
            (binding.template_code, binding.template_version)
        ]
        return SelectedTemplateV1(binding=binding, template=template)


def _validate_template(template: DocumentTemplateV1) -> None:
    if template.contract_version != 1:
        raise TemplateInvalid(
            f"template {template.template_code!r} has unsupported contract version "
            f"{template.contract_version}"
        )
    if not template.template_code.strip() or not template.template_version.strip():
        raise TemplateInvalid("template code and version must be non-empty")
    labels: dict[str, str] = {}
    for label in template.labels:
        if not label.key.strip() or not label.text.strip():
            raise TemplateInvalid("template labels must have non-empty keys and text")
        if label.key in labels:
            raise TemplateInvalid(f"duplicate template label {label.key!r}")
        labels[label.key] = label.text
    missing = sorted(_REQUIRED_LABELS - set(labels))
    if missing:
        raise TemplateInvalid(f"template is missing required labels: {missing}")
    for format_code in (template.date_format_code, template.datetime_format_code):
        if not format_code.strip():
            raise TemplateInvalid("date format codes must be non-empty")
        if any(token in format_code for token in _LOCALE_DEPENDENT_FORMATS):
            raise TemplateInvalid(
                f"format {format_code!r} depends on the host locale; "
                "P9 is not available"
            )


def label_map(template: DocumentTemplateV1) -> MappingProxyType[str, str]:
    return MappingProxyType({label.key: label.text for label in template.labels})


__all__ = ["DEFAULT_ENGLISH_LABELS", "TemplateCatalog", "label_map"]
