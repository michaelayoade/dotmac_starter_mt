"""Public contract for dotmac-payroll."""

from dotmac_payroll.contracts import (
    EmployeeComponentInput,
    PayComponentInput,
    PayRuleInput,
)
from dotmac_payroll.manifest import module
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
    record_liability_settlement,
    retire_pay_component,
    update_pay_component,
)

__version__ = "0.1.0a1"

__all__ = [
    "EmployeeComponentInput",
    "PayComponentInput",
    "PayRuleInput",
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
    "record_liability_settlement",
    "retire_pay_component",
    "update_pay_component",
]
