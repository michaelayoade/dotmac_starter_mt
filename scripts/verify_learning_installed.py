"""Hosted exact-wheel pair proof; authored, never executed locally.

The expectation command reads source AST before wheel construction. The probe
is copied outside the checkout and imports only installed public owner APIs.
Fixtures express test policy, never Academy operation/adoption authority.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib
import importlib.metadata
import json
import os
import re
import site
import sys
import tempfile
import tomllib
import zipfile
from datetime import UTC, datetime
from email.parser import BytesParser
from pathlib import Path
from uuid import uuid4


def require(value: object, message: str) -> None:
    if not value:
        raise RuntimeError(message)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def expectation(checkout: Path, destination: Path) -> None:
    """Derive names AND alias bindings independently of the installed artifact."""
    source = checkout / "packages/dotmac-learning/src/dotmac_learning/__init__.py"
    tree = ast.parse(source.read_text())
    bindings = {}
    exports = None
    version = None
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            require(node.level <= 1, "Source root relative owner escapes package")
            if node.level == 1:
                owner = (
                    f"dotmac_learning.{node.module}"
                    if node.module
                    else "dotmac_learning"
                )
            else:
                owner = node.module
            for alias in node.names:
                bindings[alias.asname or alias.name] = [owner, alias.name]
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    exports = ast.literal_eval(node.value)
                if isinstance(target, ast.Name) and target.id == "__version__":
                    version = ast.literal_eval(node.value)
    require(
        exports is not None and len(exports) == len(set(exports)) == 535,
        "Source Learning export extent differs",
    )
    require(
        set(exports) - set(bindings) == {"__version__"},
        "Unresolved source root owner binding",
    )
    document = {
        "schema": "LearningInstalledExpectation.v1",
        "source_sha": os.environ["GITHUB_SHA"],
        "base_sha": os.environ["LEARNING_BASE_SHA"],
        "root_source_sha256": digest(source),
        "kernel_root_source_sha256": digest(
            checkout / "packages/dotmac-kernel/src/dotmac_kernel/__init__.py"
        ),
        "kernel_requirement": tomllib.loads(
            (checkout / "packages/dotmac-learning/pyproject.toml").read_text()
        )["tool"]["poetry"]["dependencies"]["dotmac-kernel"],
        "version": version,
        "exports": exports,
        "bindings": {name: bindings[name] for name in exports if name in bindings},
        "wheel_required": json.loads(
            (checkout / ".github/release-modules.json").read_text()
        )["modules"]["dotmac-learning"]["wheel_contents"]["required"],
    }
    destination.write_text(json.dumps(document, sort_keys=True, indent=2) + "\n")


def installed_packages(expected: dict, receipt: dict) -> None:
    import dotmac_kernel
    import dotmac_learning
    from dotmac_learning.manifest import module

    roots = tuple(Path(path).resolve() for path in site.getsitepackages())
    require(
        sys.prefix != sys.base_prefix
        and all(path.is_relative_to(Path(sys.prefix).resolve()) for path in roots),
        "Isolated site-packages required",
    )
    require(
        list(dotmac_learning.__all__) == expected["exports"],
        "Installed Learning exports differ from source expectation",
    )
    for name, (owner, exported) in expected["bindings"].items():
        require(
            getattr(dotmac_learning, name)
            is getattr(importlib.import_module(owner), exported),
            "Installed root owner identity differs",
        )
    require(len(dotmac_kernel.__all__) == 281, "Kernel export extent differs")
    for owner, names in {
        "dotmac_kernel.identity_storage": (
            "create_tenant",
            "create_person",
            "credential_member_ids",
        ),
        "dotmac_kernel.identity_provisioning": ("bootstrap_tenant",),
        "dotmac_kernel.migrations.identity_history": (
            "import_identity_history",
            "verify_identity_history",
        ),
    }.items():
        imported = importlib.import_module(owner)
        for name in names:
            require(
                callable(getattr(imported, name)), "Required Kernel public API missing"
            )
    wheels = {}
    for distribution, package, version, variable in (
        ("dotmac-kernel", dotmac_kernel, "0.1.0a106+dev", "IDENTITY"),
        ("dotmac-learning", dotmac_learning, expected["version"], "LEARNING"),
    ):
        require(
            importlib.metadata.version(distribution) == package.__version__ == version,
            "Installed version differs",
        )
        if distribution == "dotmac-learning":
            require(module.version == version, "Learning manifest version differs")
        wheel = Path(os.environ[variable + "_WHEEL_PATH"]).resolve(strict=True)
        wheel_digest = digest(wheel)
        require(
            wheel_digest == os.environ[variable + "_WHEEL_SHA256"],
            "Wheel digest differs",
        )
        package_root = Path(package.__file__).resolve().parent.parent
        with zipfile.ZipFile(wheel) as archive:
            metadata = [
                n for n in archive.namelist() if n.endswith(".dist-info/METADATA")
            ]
            require(len(metadata) == 1, "Wheel metadata extent differs")
            message = BytesParser().parsebytes(archive.read(metadata[0]))
            require(
                message["Name"] == distribution and message["Version"] == version,
                "Wheel metadata identity differs",
            )
            for name in archive.namelist():
                if name.endswith("/") or not name.endswith((".py", ".json", ".toml")):
                    continue
                if name.startswith(
                    (
                        "dotmac_kernel/",
                        "dotmac_learning/",
                        "dotmac_learning_bank_rules/",
                    )
                ):
                    installed = package_root / name
                    require(
                        installed.is_file()
                        and installed.read_bytes() == archive.read(name),
                        "Installed bytes differ from wheel",
                    )
            if distribution == "dotmac-kernel":
                require(
                    hashlib.sha256(
                        archive.read("dotmac_kernel/__init__.py")
                    ).hexdigest()
                    == expected["kernel_root_source_sha256"],
                    "Kernel source wheel digest differs",
                )
            if distribution == "dotmac-learning":
                requirements = message.get_all("Requires-Dist", [])
                require(
                    expected["kernel_requirement"] == ">=0.1.0a106"
                    and any(
                        re.fullmatch(
                            r"dotmac-kernel\s*\(?\s*>=0\.1\.0a106\s*\)?", value
                        )
                        for value in requirements
                    ),
                    "Published Kernel floor differs",
                )
                require(
                    set(expected["wheel_required"]) <= set(archive.namelist()),
                    "Learning required wheel extent absent",
                )
                require(
                    any(
                        n.startswith("dotmac_learning_bank_rules/")
                        for n in archive.namelist()
                    ),
                    "Bank rules package absent",
                )
                require(
                    hashlib.sha256(
                        archive.read("dotmac_learning/__init__.py")
                    ).hexdigest()
                    == expected["root_source_sha256"],
                    "Root source wheel digest differs",
                )
        wheels[distribution] = {"sha256": wheel_digest, "version": version}
    receipt.update(
        wheels=wheels,
        learning_exports=535,
        kernel_exports=281,
        owner_binding_sha256=hashlib.sha256(
            json.dumps(expected["bindings"], sort_keys=True).encode()
        ).hexdigest(),
    )
    check_origins(roots)


def check_origins(roots: tuple[Path, ...]) -> None:
    for name, module in tuple(sys.modules.items()):
        if name.split(".")[0] not in {
            "dotmac_kernel",
            "dotmac_learning",
            "dotmac_learning_bank_rules",
        }:
            continue
        origin = getattr(module, "__file__", None)
        require(
            origin
            and any(
                Path(origin).resolve(strict=True).is_relative_to(root) for root in roots
            ),
            "Source or foreign package origin refused",
        )


def database_urls():
    from sqlalchemy.engine import URL, make_url

    run, attempt = os.environ["GITHUB_RUN_ID"], os.environ["GITHUB_RUN_ATTEMPT"]
    require(
        re.fullmatch(r"[0-9]+", run) and re.fullmatch(r"[0-9]+", attempt),
        "Run identity required",
    )
    host, port = os.environ["TEST_DB_HOST"], int(os.environ["TEST_DB_PORT"])
    name = os.environ["LEARNING_PAIR_DB_NAME"]
    require(
        host == "localhost"
        and 0 < port < 65536
        and len(name) <= 63
        and name == f"starter_learning_pair_{run}_{attempt}",
        "Run-owned pair DB required",
    )
    require(name != os.environ["TEST_DB_NAME"], "Ordinary database reused")
    for variable in (
        "IDENTITY_REFERENCE_DATABASE_URL",
        "IDENTITY_GRAPH_DATABASE_URL",
        "IDENTITY_INSTALLED_DATABASE_URL",
    ):
        require(
            name != make_url(os.environ[variable]).database, "Identity database reused"
        )
    admin = URL.create(
        "postgresql+psycopg", username="app_admin", host=host, port=port, database=name
    )
    user = admin.set(username="app_user")
    return admin, user


def migrate(admin, receipt: dict) -> None:
    import sqlalchemy as sa
    from dotmac_kernel.migrations import versions_dir
    from dotmac_kernel.migrations.gate import run_gate
    from dotmac_kernel.migrations.verify import require_prerequisites
    from dotmac_kernel.planes import install_module_plane_selections
    from dotmac_kernel.prerequisites import (
        PrerequisiteBinding,
        install_prerequisite_bindings,
    )
    from dotmac_learning.manifest import module
    from dotmac_learning.migrations import versions_dir as learning_versions

    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    bindings = tuple(
        PrerequisiteBinding(name, "0030_credential_tenant_key", "kernel")
        for name in module.requires
    )
    install_module_plane_selections(())
    install_prerequisite_bindings(bindings)
    locations = (versions_dir(), learning_versions())
    report = run_gate((module,), locations, bindings=bindings, module_planes=())
    require(report.ok, "Installed public migration gate refused")
    engine = sa.create_engine(admin, hide_parameters=True)
    observations = []
    try:
        with tempfile.TemporaryDirectory() as directory:
            environment = Path(directory)
            (environment / "env.py").write_text(
                "from alembic import context\nconfig=context.config\n"
                "context.configure(connection=config.attributes['connection'],target_metadata=None,"
                "version_table_schema='public',transactional_ddl=True,on_version_apply=config.attributes['observe'])\n"
                "with context.begin_transaction():\n    context.run_migrations()\n"
            )
            cfg = Config()
            cfg.set_main_option("script_location", str(environment))
            cfg.set_main_option("version_locations", " ".join(map(str, locations)))
            scripts = ScriptDirectory.from_config(cfg)
            require(
                set(scripts.get_heads())
                == {"0030_credential_tenant_key", "lr_0007_enrollment_state_policy"},
                "Installed pair graph head differs",
            )

            def observe(*, ctx, step, heads, run_args):
                for revision in step.up_revision_ids:
                    observations.append(
                        {
                            "revision": revision,
                            "sha256": digest(Path(scripts.get_revision(revision).path)),
                        }
                    )

            cfg.attributes["observe"] = observe
            with engine.begin() as connection:
                require(
                    connection.scalar(
                        sa.text(
                            "SELECT current_user='app_admin' "
                            "AND session_user='app_admin'"
                        )
                    ),
                    "Genuine app_admin required",
                )
                require(
                    connection.scalar(
                        sa.text(
                            "SELECT NOT EXISTS (SELECT FROM pg_class c "
                            "JOIN pg_namespace n ON n.oid=c.relnamespace "
                            "WHERE n.nspname NOT IN "
                            "('pg_catalog','information_schema') "
                            "AND n.nspname NOT LIKE 'pg_toast%')"
                        )
                    ),
                    "Pair database must be empty",
                )
                cfg.attributes["connection"] = connection
                command.upgrade(cfg, "0030_credential_tenant_key")
                command.upgrade(cfg, "lr_0007_enrollment_state_policy")
                require_prerequisites(connection, module.requires)
                require(
                    set(
                        connection.scalars(
                            sa.text("SELECT version_num FROM public.alembic_version")
                        )
                    )
                    == {"lr_0007_enrollment_state_policy"},
                    "Live installed head differs",
                )
                require(
                    connection.scalar(
                        sa.text(
                            "SELECT count(*)=27 AND bool_and(c.relrowsecurity) "
                            "FROM pg_class c "
                            "JOIN pg_namespace n ON n.oid=c.relnamespace "
                            "WHERE n.nspname='mod_learning' AND c.relkind='r'"
                        )
                    ),
                    "Learning RLS catalog differs",
                )
            revisions = {script.revision for script in scripts.walk_revisions()}
            require(
                len(observations) == len(revisions)
                and {o["revision"] for o in observations} == revisions,
                "Actual applied revision extent differs",
            )
        receipt.update(
            migration_revisions=observations,
            public_gate=True,
            actual_roles_and_rls=True,
        )
    finally:
        engine.dispose()


def vertical(admin_url, user_url, receipt: dict) -> None:
    import sqlalchemy as sa
    from dotmac_kernel.audit import AuditEvent
    from dotmac_kernel.audit_actions import AuditActionRegistry, install_audit_actions
    from dotmac_kernel.identity_storage import (
        NewPersonValues,
        NewTenantValues,
        create_person,
        create_tenant,
    )
    from dotmac_learning import (
        ActivityWrite,
        AdmissionEnrollmentInitialState,
        AdmissionRefused,
        BulkEnrollCommand,
        BulkEnrollmentInitialState,
        CertificateCommand,
        CompletionCommand,
        CourseWrite,
        EnsureAdmissionEnrollmentCommand,
        EventWrite,
        GradedResult,
        LabResultCommand,
        LabResultRead,
        RosterAuthority,
        SubmitMCQCommand,
        bulk_enroll,
        create_activity,
        create_course,
        ensure_admission_enrollment,
        events,
        get_certificate,
        get_completion,
        get_course,
        issue_certificate,
        read_lab_result,
        recompute_completion,
        record_lab_result,
        submit_mcq_activity,
        success_queue,
    )
    from dotmac_learning.account_contracts import AccountPresenceSelection
    from dotmac_learning.attempt_contracts import AttemptAuditContext
    from dotmac_learning.curriculum_models import Question, QuestionBank
    from dotmac_learning.delivery_models import SuccessQueueEntry
    from dotmac_learning.manifest import module
    from dotmac_learning.models import (
        Certificate,
        CourseCompletion,
        LearningEvent,
        Score,
        Submission,
    )
    from dotmac_learning.participation_models import Cohort, Enrollment
    from dotmac_learning.projections import best_scores_for
    from sqlalchemy.orm import Session

    install_audit_actions(AuditActionRegistry.from_manifests((module,)))
    admin = sa.create_engine(admin_url, hide_parameters=True)
    user = sa.create_engine(user_url, hide_parameters=True)
    tenant, other, person, course, mcq, lab = (uuid4() for _ in range(6))
    now = datetime.now(UTC)
    email = "synthetic-pair@example.test"

    def scope(db, value):
        db.execute(
            sa.text("SELECT set_config('app.current_tenant', :tenant, true)"),
            {"tenant": str(value)},
        )
        require(
            db.scalar(
                sa.text(
                    "SELECT current_user='app_user' AND NOT rolsuper "
                    "AND NOT rolbypassrls "
                    "FROM pg_roles WHERE rolname=current_user"
                )
            ),
            "Genuine non-bypass app_user required",
        )

    def counts(db):
        return tuple(
            db.scalar(sa.select(sa.func.count()).select_from(model))
            for model in (
                Submission,
                Score,
                Certificate,
                LearningEvent,
                CourseCompletion,
                Enrollment,
                SuccessQueueEntry,
                AuditEvent,
            )
        )

    try:
        with Session(admin) as db, db.begin():
            for value in (tenant, other):
                create_tenant(
                    db,
                    values=NewTenantValues(
                        value, value.hex, "Synthetic pair", True, now, now
                    ),
                )
            create_person(
                db,
                values=NewPersonValues(
                    person,
                    tenant,
                    email,
                    "Synthetic pair",
                    "Pair",
                    "Probe",
                    True,
                    {},
                    now,
                    now,
                ),
            )
        # Consumer-owned account storage, with its own explicit presence predicate.
        with admin.begin() as conn:
            conn.execute(sa.text("CREATE SCHEMA pair_consumer AUTHORIZATION app_admin"))
            conn.execute(
                sa.text(
                    "CREATE TABLE pair_consumer.accounts "
                    "(person_id uuid PRIMARY KEY, tenant_id uuid NOT NULL, "
                    "present boolean NOT NULL)"
                )
            )
            conn.execute(
                sa.text("ALTER TABLE pair_consumer.accounts ENABLE ROW LEVEL SECURITY")
            )
            conn.execute(
                sa.text(
                    "CREATE POLICY tenant_scope ON pair_consumer.accounts "
                    "USING (tenant_id = "
                    "current_setting('app.current_tenant', true)::uuid)"
                )
            )
            conn.execute(sa.text("GRANT USAGE ON SCHEMA pair_consumer TO app_user"))
            conn.execute(sa.text("GRANT SELECT ON pair_consumer.accounts TO app_user"))
            conn.execute(
                sa.text(
                    "INSERT INTO pair_consumer.accounts VALUES (:person, :tenant, true)"
                ),
                {"person": person, "tenant": tenant},
            )
        account = sa.Table(
            "accounts",
            sa.MetaData(),
            sa.Column("person_id", sa.Uuid),
            sa.Column("tenant_id", sa.Uuid),
            sa.Column("present", sa.Boolean),
            schema="pair_consumer",
        )
        with Session(user) as db:
            root = db.begin()
            try:
                scope(db, tenant)
                create_course(
                    db,
                    CourseWrite(
                        id=course,
                        tenant_id=tenant,
                        slug=course.hex,
                        title="Installed course",
                        discipline="engineering",
                        source_ref="synthetic-installed",
                        status="published",
                        listed=False,
                    ),
                )
                bank = QuestionBank(
                    tenant_id=tenant, course_id=course, kind="practice", version=1
                )
                db.add(bank)
                db.flush()
                question = Question(
                    tenant_id=tenant,
                    bank_id=bank.id,
                    ext_id="q",
                    stem="S",
                    type="single",
                    rubric_category="recall",
                    correct=["yes"],
                    options=["yes", "no"],
                    weight=1,
                )
                db.add(question)
                for activity, kind in ((mcq, "mcq_test"), (lab, "lab")):
                    create_activity(
                        db,
                        ActivityWrite(
                            id=activity,
                            tenant_id=tenant,
                            course_id=course,
                            title="Installed activity",
                            type=kind,
                            assessment_mode="graded",
                            grading="auto",
                            weight=1.0,
                            max_attempts=1,
                            pass_threshold=0.7,
                            bank_id=bank.id if kind == "mcq_test" else None,
                        ),
                    )
                db.flush()
                db.commit()
                root = db.begin()
                scope(db, tenant)
                command = SubmitMCQCommand(
                    tenant_id=tenant,
                    person_id=person,
                    activity_id=mcq,
                    answers={"q": ["yes"]},
                    missing_question_weight=1,
                )
                result = submit_mcq_activity(db, command)
                require(
                    result.score is not None and result.score.passed,
                    "Actual MCQ grading failed",
                )
                require(
                    result.completion is not None
                    and result.completion.completion.status == "in_progress"
                    and result.completion.certificate is None,
                    "Final lab must gate completion",
                )
                require(
                    best_scores_for(
                        db, tenant_id=tenant, person_id=person, course_id=course
                    ),
                    "Best score absent",
                )
                before = counts(db)
                try:
                    submit_mcq_activity(db, command)
                except AdmissionRefused:
                    pass
                else:
                    raise RuntimeError("Actual attempt policy did not refuse")
                require(counts(db) == before, "Attempt refusal appended a result")
                # A bound synthetic producer fixture accepts grading only. Its
                # operation identifiers never establish authorization. Real Academy
                # lab operation/instance custody remains a separate adoption gate.
                instance = uuid4()

                def lab_producer(grade):
                    require(db.get_transaction() is root, "Producer caller changed")
                    return record_lab_result(
                        db,
                        LabResultCommand(
                            tenant_id=tenant,
                            person_id=person,
                            activity_id=lab,
                            instance_id=instance,
                            seed={"synthetic": True},
                            grading=grade,
                        ),
                    )

                grade = GradedResult(score=1, max_score=1, fraction=1, passed=True)
                produced = lab_producer(grade)
                # Actual ledger timestamps exercise inclusive matching. The
                # synthetic context never establishes product-operation authority.
                read_values = {
                    "tenant_id": tenant,
                    "person_id": person,
                    "activity_id": lab,
                    "instance_id": instance,
                    "claimed_at": produced.submission.created_at,
                    "finished_at": produced.submission.created_at,
                }
                read_before = counts(db)
                read_nested = db.get_nested_transaction()
                require(
                    produced.score is not None
                    and read_lab_result(db, LabResultRead(**read_values))
                    == produced.score,
                    "Installed owned-instance ledger read differs",
                )
                require(
                    read_lab_result(
                        db,
                        LabResultRead(**dict(read_values, instance_id=uuid4())),
                    )
                    is None,
                    "Installed ledger read crossed instance selector",
                )
                require(
                    read_lab_result(
                        db,
                        LabResultRead(**dict(read_values, finished_at=None)),
                    )
                    is None,
                    "Installed ledger read accepted an absent worker bound",
                )
                require(
                    counts(db) == read_before
                    and db.get_transaction() is root
                    and db.get_nested_transaction() is read_nested,
                    "Installed ledger read changed effects or caller boundary",
                )
                receipt["lab_result_read"] = True
                require(
                    produced.completion is not None
                    and produced.completion.first_completion
                    and produced.completion.certificate is not None
                    and produced.completion.certificate.created,
                    "Real final-lab consequence absent",
                )
                certificate = produced.completion.certificate.certificate
                before_append = counts(db)
                appended = lab_producer(grade)
                require(
                    appended.submission.id != produced.submission.id
                    and counts(db)[0] == before_append[0] + 1
                    and counts(db)[1] == before_append[1] + 1,
                    "Lab result must retain append-attempt behavior",
                )
                best = best_scores_for(
                    db, tenant_id=tenant, person_id=person, course_id=course
                )
                require(
                    set(best) == {mcq, lab} and best[lab].passed,
                    "Actual best-result projection differs",
                )
                before = counts(db)
                repeated = recompute_completion(
                    db,
                    CompletionCommand(
                        tenant_id=tenant, person_id=person, course_id=course
                    ),
                )
                issued = issue_certificate(
                    db,
                    CertificateCommand(
                        tenant_id=tenant, person_id=person, course_id=course
                    ),
                )
                require(
                    not repeated.first_completion
                    and not issued.created
                    and issued.certificate == certificate,
                    "Repeated certificate behavior differs",
                )
                require(
                    get_certificate(
                        db, tenant_id=tenant, person_id=person, course_id=course
                    )
                    == certificate,
                    "Certificate read differs",
                )
                require(
                    get_completion(
                        db, tenant_id=tenant, person_id=person, course_id=course
                    ).status
                    == "completed",
                    "Completion read differs",
                )
                require(
                    events.record(
                        db,
                        EventWrite(
                            tenant_id=tenant,
                            person_id=person,
                            course_id=course,
                            subject_id=course,
                            kind="certificate_earned",
                        ),
                    )
                    is None,
                    "Existing certificate event dedupe failed",
                )
                require(
                    counts(db) == before,
                    "Repeated completion or certificate appended facts",
                )
                cohort = Cohort(
                    tenant_id=tenant, name="Installed cohort", discipline="engineering"
                )
                db.add(cohort)
                db.flush()
                admission = EnsureAdmissionEnrollmentCommand(
                    tenant_id=tenant,
                    person_id=person,
                    cohort_id=cohort.id,
                    track_id=None,
                )
                enrolled = ensure_admission_enrollment(
                    db,
                    admission,
                    initial_state=AdmissionEnrollmentInitialState(
                        "instructor", "dropped"
                    ),
                )
                preserved = ensure_admission_enrollment(
                    db,
                    admission,
                    initial_state=AdmissionEnrollmentInitialState("student", "active"),
                )
                require(
                    enrolled == preserved
                    and preserved.role_in_cohort == "instructor"
                    and preserved.status == "dropped",
                    "Existing enrollment overwritten",
                )
                presence = AccountPresenceSelection(
                    tenant,
                    sa.select(account.c.person_id).where(
                        account.c.tenant_id == tenant, account.c.present.is_(True)
                    ),
                )
                refusal = bulk_enroll(
                    db,
                    BulkEnrollCommand(
                        tenant_id=tenant, cohort_id=cohort.id, emails=(email,)
                    ),
                    authority=RosterAuthority(tenant, False),
                    accounts=presence,
                    initial_state=BulkEnrollmentInitialState("student", "active"),
                )
                require(
                    refusal.admin_required == (email,)
                    and db.get(Enrollment, enrolled.id).status == "dropped",
                    "Non-admin non-student reactivation admitted",
                )
                # Each supported API performs one genuine registered Kernel audit.
                for operation, receipt_api in (
                    (success_queue.transition, False),
                    (success_queue.transition_with_receipt, True),
                ):
                    entry = SuccessQueueEntry(
                        tenant_id=tenant,
                        person_id=person,
                        reason_kind="inactivity",
                        supporting_facts={},
                        severity="medium",
                        detected_at=now,
                        status="open",
                    )
                    db.add(entry)
                    db.flush()
                    audit_before = db.scalar(
                        sa.select(sa.func.count()).select_from(AuditEvent)
                    )
                    effect = operation(
                        db,
                        tenant_id=tenant,
                        entry_id=entry.id,
                        action="acknowledge",
                        actor_person_id=person,
                        audit_context=AttemptAuditContext(
                            tenant, "system", "installed_pair"
                        ),
                    )
                    require(
                        db.scalar(sa.select(sa.func.count()).select_from(AuditEvent))
                        == audit_before + 1,
                        "Actual audit effect count differs",
                    )
                    if receipt_api:
                        audit = db.get(AuditEvent, effect.audit_event_id)
                        require(
                            audit is not None
                            and audit.action == "success_queue.acknowledge"
                            and audit.entity_id == str(entry.id),
                            "Actual audit receipt differs",
                        )
                # Genuine required engine error after append, without replacing a
                # grader or installing a fake policy. The unchanged engine raises
                # TypeError on this accepted malformed JSON answer, as in source tests.
                bad = uuid4()
                create_activity(
                    db,
                    ActivityWrite(
                        id=bad,
                        tenant_id=tenant,
                        course_id=course,
                        title="Required failure",
                        type="mcq_test",
                        assessment_mode="graded",
                        grading="auto",
                        weight=1,
                        max_attempts=1,
                        pass_threshold=0.7,
                        bank_id=bank.id,
                    ),
                )
                before_failure = counts(db)
                try:
                    submit_mcq_activity(
                        db,
                        SubmitMCQCommand(
                            tenant_id=tenant,
                            person_id=person,
                            activity_id=bad,
                            answers={"q": [{}]},
                            missing_question_weight=1,
                        ),
                    )
                except TypeError as failure:
                    require(
                        type(failure) is TypeError, "Required engine exception changed"
                    )
                else:
                    raise RuntimeError("Required grading error did not propagate")
                require(
                    counts(db)[0] == before_failure[0] + 1,
                    "Required failure append boundary not observed",
                )
                require(
                    db.get_transaction() is root
                    and root.is_active
                    and not db.in_nested_transaction(),
                    "Caller root changed",
                )
            finally:
                root.rollback()
        # Independent connections observe rollback and actual RLS isolation.
        with Session(user) as db, db.begin():
            scope(db, tenant)
            require(
                get_course(db, tenant_id=tenant, course_id=course) is not None
                and counts(db) == (0,) * 8,
                "Caller rollback left durable facts",
            )
        with Session(user) as db, db.begin():
            scope(db, other)
            require(
                get_course(db, tenant_id=tenant, course_id=course) is None,
                "Durable course leaked across tenants",
            )
            require(
                db.scalar(sa.select(sa.func.count()).select_from(account)) == 0,
                "Consumer tenant isolation failed",
            )
            try:
                submit_mcq_activity(
                    db,
                    SubmitMCQCommand(
                        tenant_id=tenant,
                        person_id=person,
                        activity_id=mcq,
                        missing_question_weight=1,
                    ),
                )
            except ValueError:
                pass
            else:
                raise RuntimeError("Tenant-scoped MCQ refusal absent")
        receipt.update(
            mcq_engine_and_attempt_policy=True,
            final_lab_completion=True,
            result_append_attempt_behavior=True,
            repeated_certificate_and_event_dedupe=True,
            explicit_state_and_account_presence=True,
            non_admin_refusal=True,
            old_and_receipt_api_actual_audit=True,
            required_engine_failure_propagates=True,
            caller_root_rollback=True,
            independent_nondurability=True,
            tenant_isolation=True,
        )
    finally:
        user.dispose()
        admin.dispose()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-expectation", type=Path)
    parser.add_argument("--checkout", type=Path)
    args = parser.parse_args()
    require(
        os.environ.get("CI") == "true" and os.environ.get("GITHUB_ACTIONS") == "true",
        "Hosted CI required",
    )
    if args.write_expectation:
        expectation(args.checkout.resolve(strict=True), args.write_expectation)
        return 0
    receipt = {
        "schema": "LearningInstalledPair.v1",
        "status": "failed",
        "authority": "candidate-only",
    }
    destination = Path(os.environ["LEARNING_INSTALLED_RECEIPT"])
    try:
        checkout = Path(os.environ["GITHUB_WORKSPACE"]).resolve()
        require(
            "PYTHONPATH" not in os.environ
            and not Path.cwd().resolve().is_relative_to(checkout)
            and not Path(__file__).resolve().is_relative_to(checkout)
            and all(
                not Path(p).resolve().is_relative_to(checkout) for p in sys.path if p
            ),
            "Outside-checkout isolated probe required",
        )
        expected_path = Path(os.environ["LEARNING_EXPECTATION_PATH"])
        expected = json.loads(expected_path.read_text())
        require(
            expected["schema"] == "LearningInstalledExpectation.v1"
            and re.fullmatch(r"[0-9a-f]{40}", expected["source_sha"])
            and expected["source_sha"] == os.environ["GITHUB_SHA"]
            and expected["base_sha"] == os.environ["LEARNING_BASE_SHA"],
            "Exact source expectation differs",
        )
        receipt.update(
            source_sha=expected["source_sha"],
            base_sha=expected["base_sha"],
            expectation_sha256=digest(expected_path),
        )
        installed_packages(expected, receipt)
        admin, user = database_urls()
        migrate(admin, receipt)
        vertical(admin, user, receipt)
        check_origins(tuple(Path(path).resolve() for path in site.getsitepackages()))
        receipt["status"] = "passed"
        return 0
    except Exception:
        print("Installed Learning pair qualification failed", file=sys.stderr)
        return 1
    finally:
        destination.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
