"""Public typed surface for ``dotmac-forms``."""

from dotmac_forms.contracts import (
    AnswerInput,
    ContractError,
    FieldDefinition,
    FieldType,
    FormDefinition,
    OptionDefinition,
    SectionDefinition,
    SubmissionRequest,
)
from dotmac_forms.manifest import module
from dotmac_forms.migrations import versions_dir
from dotmac_forms.service import (
    FormError,
    FormUnavailable,
    FormValidationError,
    SubmissionConflict,
    SubmissionReceipt,
    add_field,
    add_option,
    add_section,
    create_draft_version,
    create_form,
    publish_version,
    submit_form,
)

__version__ = "0.1.0a1"

__all__ = [
    "AnswerInput",
    "ContractError",
    "FieldDefinition",
    "FieldType",
    "FormDefinition",
    "FormError",
    "FormUnavailable",
    "FormValidationError",
    "OptionDefinition",
    "SectionDefinition",
    "SubmissionConflict",
    "SubmissionReceipt",
    "SubmissionRequest",
    "__version__",
    "add_field",
    "add_option",
    "add_section",
    "create_draft_version",
    "create_form",
    "module",
    "publish_version",
    "submit_form",
    "versions_dir",
]
