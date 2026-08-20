"""Public surface for tenant fixed-asset accounting."""

from dotmac_finance.calculation import (
    DepreciationResult,
    DisposalResult,
    FinanceRuleViolation,
    ImpairmentResult,
    RevaluationResult,
    calculate_depreciation,
    calculate_disposal,
    calculate_impairment,
    calculate_revaluation,
)
from dotmac_finance.contracts import (
    AccountingModel,
    AccountMapping,
    BookStatus,
    CapitalizeAssetBook,
    DepreciationMethod,
    DisposalCommand,
    ImpairmentCommand,
    RevaluationCommand,
)
from dotmac_finance.manifest import module
from dotmac_finance.service import (
    FinanceConflict,
    FinanceNotFound,
    calculate_depreciation_run,
    capitalize_asset_book,
    dispose_asset_book,
    impair_asset_book,
    post_depreciation_run,
    revalue_asset_book,
)

__version__ = "0.1.0a1"

__all__ = [
    "AccountMapping",
    "AccountingModel",
    "BookStatus",
    "CapitalizeAssetBook",
    "DepreciationMethod",
    "DepreciationResult",
    "DisposalCommand",
    "DisposalResult",
    "FinanceConflict",
    "FinanceNotFound",
    "FinanceRuleViolation",
    "ImpairmentCommand",
    "ImpairmentResult",
    "RevaluationCommand",
    "RevaluationResult",
    "calculate_depreciation",
    "calculate_depreciation_run",
    "calculate_disposal",
    "calculate_impairment",
    "calculate_revaluation",
    "capitalize_asset_book",
    "dispose_asset_book",
    "impair_asset_book",
    "module",
    "post_depreciation_run",
    "revalue_asset_book",
    "__version__",
]
