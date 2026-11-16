"""Public contract for dotmac-payroll."""

from dotmac_payroll.consequences import (
    CONSEQUENCE_OWNER,
    CONSEQUENCE_SOURCE_VERSION,
    RUN_DOCUMENT_KIND,
    SETTLEMENT_DOCUMENT_KIND,
    PayrollAccountingConsequence,
    PayrollAccountingEntry,
    PayrollConsequenceSource,
    build_liability_settlement_consequence,
    build_run_accounting_consequence,
)
from dotmac_payroll.contracts import (
    EmployeeComponentInput,
    PayComponentInput,
    PayRuleInput,
)
from dotmac_payroll.manifest import module
from dotmac_payroll.migrations import versions_dir
from dotmac_payroll.service import (
    PayrollConflict,
    PayrollNotFound,
    PayrollRuleViolation,
    approve_payroll_run,
    assign_employee_pay_structure,
    calculate_employee_payroll,
    cancel_payroll_run,
    create_pay_component,
    create_pay_structure,
    create_payroll_run,
    finalize_payroll_run,
    publish_pay_structure_revision,
    read_payroll_run_totals,
    record_liability_settlement,
    retire_pay_component,
    update_pay_component,
)
from dotmac_payroll.totals import (
    PayrollAmountSnapshot,
    PayrollRunTotals,
    PayrollTotalsViolation,
    aggregate_payroll_run_totals,
    validate_payment_total,
)

__version__ = "0.1.0a4"

__all__ = [
    "CONSEQUENCE_OWNER",
    "CONSEQUENCE_SOURCE_VERSION",
    "RUN_DOCUMENT_KIND",
    "SETTLEMENT_DOCUMENT_KIND",
    "EmployeeComponentInput",
    "PayComponentInput",
    "PayRuleInput",
    "PayrollAccountingConsequence",
    "PayrollAccountingEntry",
    "PayrollAmountSnapshot",
    "PayrollConflict",
    "PayrollConsequenceSource",
    "PayrollNotFound",
    "PayrollRuleViolation",
    "PayrollRunTotals",
    "PayrollTotalsViolation",
    "__version__",
    "aggregate_payroll_run_totals",
    "approve_payroll_run",
    "assign_employee_pay_structure",
    "build_liability_settlement_consequence",
    "build_run_accounting_consequence",
    "calculate_employee_payroll",
    "cancel_payroll_run",
    "create_pay_component",
    "create_pay_structure",
    "create_payroll_run",
    "finalize_payroll_run",
    "module",
    "publish_pay_structure_revision",
    "read_payroll_run_totals",
    "record_liability_settlement",
    "retire_pay_component",
    "update_pay_component",
    "validate_payment_total",
    "versions_dir",
]
