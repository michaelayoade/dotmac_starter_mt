"""Nonexecuting enrollment relation shape; no account/auth adoption claim."""

import ast
import inspect
from uuid import UUID, uuid4

import pytest
from dotmac_kernel.models import Party
from dotmac_learning import audience
from dotmac_learning.participation_models import Enrollment
from sqlalchemy import Select, select
from sqlalchemy.dialects import postgresql


def test_relation_factory_has_only_keyword_scope_and_no_execution_boundary():
    factory = audience.active_student_person_ids_select
    signature = inspect.signature(factory)
    assert tuple(signature.parameters) == ("tenant_id", "cohort_id")
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in signature.parameters.values()
    )
    assert signature.parameters["tenant_id"].default is inspect.Parameter.empty
    assert signature.parameters["cohort_id"].default is None
    tree = ast.parse(inspect.getsource(factory))
    calls = [node.func for node in ast.walk(tree) if isinstance(node, ast.Call)]
    assert all(
        (isinstance(call, ast.Name) and call.id == "select")
        or (isinstance(call, ast.Attribute) and call.attr in {"where", "distinct"})
        for call in calls
    )
    assert {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} <= {
        "UUID",
        "Select",
        "tuple",
        "Enrollment",
        "select",
        "tenant_id",
        "cohort_id",
        "stmt",
    }


@pytest.mark.parametrize("cohort_scoped", [False, True])
def test_relation_is_one_distinct_uuid_column_with_exact_enrollment_predicates(
    cohort_scoped,
):
    tenant, cohort = uuid4(), uuid4() if cohort_scoped else None
    relation = audience.active_student_person_ids_select(
        tenant_id=tenant, cohort_id=cohort
    )
    assert isinstance(relation, Select)
    columns = tuple(relation.selected_columns)
    assert len(columns) == 1
    assert columns[0].shares_lineage(Enrollment.person_id.__clause_element__())
    assert columns[0].type.python_type is UUID
    compiled = relation.compile(dialect=postgresql.dialect())
    sql = " ".join(str(compiled).split())
    expected = (
        "SELECT DISTINCT mod_learning.enrollments.person_id "
        "FROM mod_learning.enrollments "
        "WHERE mod_learning.enrollments.tenant_id = %(tenant_id_1)s::UUID "
        "AND mod_learning.enrollments.status = %(status_1)s "
        "AND mod_learning.enrollments.role_in_cohort = %(role_in_cohort_1)s"
    )
    params = {
        "tenant_id_1": tenant,
        "status_1": "active",
        "role_in_cohort_1": "student",
    }
    if cohort_scoped:
        expected += " AND mod_learning.enrollments.cohort_id = %(cohort_id_1)s::UUID"
        params["cohort_id_1"] = cohort
    assert sql == expected
    assert compiled.params == params


def test_relation_embeds_in_a_single_outer_identity_statement():
    tenant = uuid4()
    relation = audience.active_student_person_ids_select(tenant_id=tenant)
    outer = select(Party.id).where(Party.tenant_id == tenant, Party.id.in_(relation))
    sql = str(outer.compile(dialect=postgresql.dialect()))
    assert sql.count("SELECT") == 2  # Outer statement and embedded relation.
    assert "IN (SELECT DISTINCT mod_learning.enrollments.person_id" in sql
    assert ";" not in sql
