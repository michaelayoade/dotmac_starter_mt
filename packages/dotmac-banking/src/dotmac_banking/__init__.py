"""Public contract for dotmac-banking."""

from dotmac_banking.contracts import (
    BankAccountInput,
    BankInstitutionInput,
    BankStatementInput,
    CashObservationInput,
    MatchPolicyInput,
    MatchSuggestion,
    StatementLineDirection,
    StatementLineInput,
)
from dotmac_banking.manifest import module
from dotmac_banking.service import (
    BankingConflict,
    BankingNotFound,
    MatchRuleViolation,
    accept_match,
    approve_reconciliation,
    close_bank_account,
    create_bank_account,
    create_bank_institution,
    create_match_policy,
    import_bank_statement,
    list_bank_accounts,
    prepare_reconciliation,
    record_cash_observation,
    retire_bank_institution,
    suggest_matches,
    update_bank_account,
    update_bank_institution,
)

__version__ = "0.1.0a1"

__all__ = [
    "BankAccountInput",
    "BankInstitutionInput",
    "BankStatementInput",
    "BankingConflict",
    "BankingNotFound",
    "CashObservationInput",
    "MatchPolicyInput",
    "MatchRuleViolation",
    "MatchSuggestion",
    "StatementLineDirection",
    "StatementLineInput",
    "__version__",
    "accept_match",
    "approve_reconciliation",
    "close_bank_account",
    "create_bank_account",
    "create_bank_institution",
    "create_match_policy",
    "import_bank_statement",
    "list_bank_accounts",
    "module",
    "prepare_reconciliation",
    "record_cash_observation",
    "retire_bank_institution",
    "suggest_matches",
    "update_bank_account",
    "update_bank_institution",
]
