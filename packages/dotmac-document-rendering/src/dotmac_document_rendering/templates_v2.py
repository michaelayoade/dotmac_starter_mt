"""Exact opaque profile bindings for V2, with immutable V1 template artifacts."""

from types import MappingProxyType

from dotmac_document_rendering.contracts import (
    DocumentTemplateV1,
    TemplateInvalid,
    TemplateNotFound,
)
from dotmac_document_rendering.contracts_v2 import (
    DocumentProfileBindingV2,
    SelectedTemplateV2,
    _require_utf8_text,
)
from dotmac_document_rendering.projection_v2 import _validate_typed_value
from dotmac_document_rendering.templates import (
    DEFAULT_ENGLISH_LABELS,
    _validate_template,
)

_V2_LABELS = (
    ("trading_name", "Trading name"),
    ("line_one", "Address line one"),
    ("line_two", "Address line two"),
    ("city", "City"),
    ("region", "Region"),
    ("postal_code", "Postal code"),
    ("country_code", "Country"),
    ("identifier_type", "Identifier type"),
    ("identifier_value", "Identifier"),
    ("issuing_country_code", "Issuing country"),
    ("identity_type", "Tax identifier type"),
    ("identity_value", "Tax identifier"),
    ("pre_tax_amount", "Pre-tax amount"),
    ("discount_total", "Discount total"),
    ("discount_code", "Discount code"),
    ("method_code", "Payment method"),
    ("routing_code", "Routing code"),
    ("account_reference", "Account reference"),
    ("narrative", "Payment instructions"),
    ("payment_link", "Payment link"),
    ("fx", "Exchange rate"),
    ("base_currency", "Base currency"),
    ("quote_currency", "Quote currency"),
    ("rate", "Rate"),
    ("rate_purpose", "Rate purpose"),
    ("observed_at", "Observed at"),
    ("effective_at", "Effective at"),
)
DEFAULT_ENGLISH_LABELS_V2 = DEFAULT_ENGLISH_LABELS + _V2_LABELS


class TemplateCatalogV2:
    def __init__(
        self,
        *,
        templates: tuple[DocumentTemplateV1, ...],
        bindings: tuple[DocumentProfileBindingV2, ...],
        assembly_file: str,
    ) -> None:
        _require_utf8_text(assembly_file, "assembly_file")
        if not assembly_file.strip():
            raise TemplateInvalid("assembly_file must name the composition fix")
        template_index: dict[tuple[str, str], DocumentTemplateV1] = {}
        for template in templates:
            _validate_typed_value(template, DocumentTemplateV1, "template")
            _validate_template(template)
            missing = {key for key, _ in _V2_LABELS} - {
                label.key for label in template.labels
            }
            if missing:
                raise TemplateInvalid(
                    f"template is missing V2 labels: {sorted(missing)}"
                )
            key = (template.template_code, template.template_version)
            if key in template_index:
                raise TemplateInvalid("duplicate template artifact")
            template_index[key] = template
        binding_index: dict[tuple[str, str, str], DocumentProfileBindingV2] = {}
        for binding in bindings:
            _validate_typed_value(binding, DocumentProfileBindingV2, "binding")
            if (
                not isinstance(binding.document_profile_version, str)
                or not binding.document_profile_version.strip()
            ):
                raise TemplateInvalid(
                    "profile version must be an opaque nonempty string"
                )
            binding_key = (
                binding.document_profile_code,
                binding.document_profile_version,
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
        document_profile_code: str,
        document_profile_version: str,
        media_type: str,
    ) -> SelectedTemplateV2:
        key = (document_profile_code, document_profile_version, media_type)
        binding = self._bindings.get(key)
        if binding is None:
            raise TemplateNotFound(
                f"no exact template binding for {key!r}; "
                f"declare it in {self.assembly_file}"
            )
        return SelectedTemplateV2(
            binding, self._templates[(binding.template_code, binding.template_version)]
        )


__all__ = ["DEFAULT_ENGLISH_LABELS_V2", "TemplateCatalogV2"]
