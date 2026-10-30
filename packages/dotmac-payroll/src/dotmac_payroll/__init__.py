"""Public contract for dotmac-payroll."""

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

__version__ = "0.1.0a2"

__all__ = [
    "EmployeeComponentInput",
    "PayComponentInput",
    "PayRuleInput",
    "PayrollAmountSnapshot",
    "PayrollRunTotals",
    "PayrollTotalsViolation",
    "aggregate_payroll_run_totals",
    "validate_payment_total",
    "PayrollConflict",
    "PayrollNotFound",
    "PayrollRuleViolation",
    "__version__",
    "approve_payroll_run",
    "assign_employee_pay_structure",
    "calculate_employee_payroll",
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
    "versions_dir",
]
