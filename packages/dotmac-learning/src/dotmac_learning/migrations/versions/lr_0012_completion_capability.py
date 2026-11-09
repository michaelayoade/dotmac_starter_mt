"""Make SQL the sole completion/certificate writer (lr_0012).

Additive over lr_0011. Retires the dated dual-implementation exception
(named for 0.1.0a6): completion recompute and certificate issuance were
implemented twice -- Python for MCQ/override, lr_0011 SQL for labs. Two
SECURITY INVOKER functions are now the single writers:

- ``mod_learning.recompute_completion_v1``: the completion recompute
  (aggregate ensure/lock, best-score winners, course policy, status
  transition) with the certificate auto-issue consequence. Semantics
  equal the lr_0011 lab tail and the Python owner path it replaces.
- ``mod_learning.issue_certificate_v1``: certificate issuance with the
  Python ``issue_certificate`` semantics (existing-row return, locked
  completion eligibility, validity from the Course row, the
  ``certificate_earned`` event isolated as an outcome). Ineligibility
  is returned as an outcome, never raised, so the Python surface can
  raise ``CertificateNotEligible`` without a SQL transaction abort.

``mod_learning.record_lab_result_v1`` is replaced with a body that
keeps its lab ledger steps and delegates the recompute/certificate
tail to ``recompute_completion_v1``; its signature, grants and
returned shape are unchanged. Python ``recompute_completion`` and
``issue_certificate`` delegate to these functions; the Python mirror
is retired.

Every function pins ``search_path = pg_catalog, pg_temp`` and
qualifies every type, relation and function name. Learning grants no
privileges: EXECUTE is revoked from PUBLIC and granted to nobody.
Product composition grants EXECUTE to the roles that call the Python
API.

Downgrade restores the lr_0011 lab function body and drops the two
new functions; no stored data is affected.
"""

from __future__ import annotations

from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import (
    MODULE_DATABASE_ROLES_V1,
    PARTY_PERSON_CATALOG_V1,
    TENANT_SCOPE_CATALOG_V1,
    resolve_depends_on,
)

from alembic import op

revision = "lr_0012_completion_capability"
down_revision = "lr_0011_lab_result_capability"
branch_labels = None
REQUIRES = (
    TENANT_SCOPE_CATALOG_V1.name,
    MODULE_DATABASE_ROLES_V1.name,
    PARTY_PERSON_CATALOG_V1.name,
)
depends_on = resolve_depends_on(REQUIRES)

_ISSUE_SIGNATURE = (
    "mod_learning.issue_certificate_v1("
    "pg_catalog.uuid, pg_catalog.uuid, pg_catalog.uuid, "
    "pg_catalog.timestamptz)"
)
_RECOMPUTE_SIGNATURE = (
    "mod_learning.recompute_completion_v1("
    "pg_catalog.uuid, pg_catalog.uuid, pg_catalog.uuid, "
    "pg_catalog.timestamptz)"
)

_ISSUE_FUNCTION = """
CREATE FUNCTION mod_learning.issue_certificate_v1(
    p_tenant_id pg_catalog.uuid,
    p_person_id pg_catalog.uuid,
    p_course_id pg_catalog.uuid,
    p_now pg_catalog.timestamptz
)
RETURNS TABLE (
    outcome pg_catalog.text,
    certificate_id pg_catalog.uuid,
    certificate_created pg_catalog.bool,
    event_status pg_catalog.text,
    event_id pg_catalog.uuid
)
LANGUAGE plpgsql
VOLATILE
SECURITY INVOKER
SET search_path = pg_catalog, pg_temp
AS $func$
#variable_conflict error
DECLARE
    v_certificate_id pg_catalog.uuid := NULL;
    v_completion_status pg_catalog.text;
    v_found pg_catalog.bool;
    v_validity_days pg_catalog.int4;
    v_serial pg_catalog.text;
    v_issued_at pg_catalog.timestamptz;
    v_party_type pg_catalog.text;
    v_event_id pg_catalog.uuid := NULL;
    v_status pg_catalog.text := NULL;
BEGIN
    IF p_tenant_id IS NULL OR p_person_id IS NULL OR p_course_id IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '22004',
            MESSAGE = 'certificate arguments must not be null';
    END IF;

    SELECT cert.id INTO v_certificate_id
    FROM mod_learning.certificates AS cert
    WHERE cert.tenant_id = p_tenant_id
        AND cert.person_id = p_person_id
        AND cert.course_id = p_course_id;
    IF v_certificate_id IS NOT NULL THEN
        outcome := 'existing';
        certificate_id := v_certificate_id;
        certificate_created := false;
        event_status := NULL;
        event_id := NULL;
        RETURN NEXT;
        RETURN;
    END IF;

    SELECT comp.status INTO v_completion_status
    FROM mod_learning.course_completions AS comp
    WHERE comp.tenant_id = p_tenant_id
        AND comp.person_id = p_person_id
        AND comp.course_id = p_course_id
    FOR UPDATE;
    v_found := FOUND;

    -- The lock wait may have let the first issuer commit; look again
    -- before judging eligibility, mirroring the Python owner path.
    SELECT cert.id INTO v_certificate_id
    FROM mod_learning.certificates AS cert
    WHERE cert.tenant_id = p_tenant_id
        AND cert.person_id = p_person_id
        AND cert.course_id = p_course_id;
    IF v_certificate_id IS NOT NULL THEN
        outcome := 'existing';
        certificate_id := v_certificate_id;
        certificate_created := false;
        event_status := NULL;
        event_id := NULL;
        RETURN NEXT;
        RETURN;
    END IF;

    IF NOT v_found OR v_completion_status IS DISTINCT FROM 'completed' THEN
        outcome := 'not_eligible';
        certificate_id := NULL;
        certificate_created := false;
        event_status := NULL;
        event_id := NULL;
        RETURN NEXT;
        RETURN;
    END IF;

    SELECT crs.certificate_validity_days INTO v_validity_days
    FROM mod_learning.courses AS crs
    WHERE crs.tenant_id = p_tenant_id AND crs.id = p_course_id;

    v_serial := 'CERT-' || pg_catalog.upper(
        pg_catalog.substr(
            pg_catalog.replace(
                pg_catalog.gen_random_uuid()::pg_catalog.text, '-', ''
            ),
            1, 12
        )
    );
    v_issued_at := COALESCE(p_now, pg_catalog.clock_timestamp());
    INSERT INTO mod_learning.certificates
        (id, tenant_id, person_id, course_id, serial, issued_at, expires_at)
    VALUES (
        pg_catalog.gen_random_uuid(), p_tenant_id, p_person_id,
        p_course_id, v_serial, v_issued_at,
        CASE WHEN v_validity_days IS NULL THEN NULL
            ELSE v_issued_at
                + pg_catalog.make_interval(days => v_validity_days)
        END
    )
    RETURNING id INTO v_certificate_id;

    v_status := 'recorded';
    BEGIN
        SELECT party.party_type::pg_catalog.text INTO v_party_type
        FROM public.parties AS party
        WHERE party.tenant_id = p_tenant_id AND party.id = p_person_id;
        IF v_party_type IS DISTINCT FROM 'person' THEN
            RAISE EXCEPTION USING ERRCODE = '22023',
                MESSAGE = 'learning event identity must be a person';
        END IF;
        INSERT INTO mod_learning.learning_events
            (id, tenant_id, person_id, course_id, kind, subject_id,
             detail, occurred_at)
        VALUES (
            pg_catalog.gen_random_uuid(), p_tenant_id, p_person_id,
            p_course_id, 'certificate_earned', p_course_id,
            pg_catalog.jsonb_build_object('serial', v_serial),
            pg_catalog.clock_timestamp()
        )
        ON CONFLICT (tenant_id, person_id, kind, subject_id)
            WHERE kind IN ('chapter_completed', 'certificate_earned')
            DO NOTHING
        RETURNING id INTO v_event_id;
        IF v_event_id IS NULL THEN
            v_status := 'suppressed';
        END IF;
    EXCEPTION WHEN OTHERS THEN
        v_status := 'failed';
        v_event_id := NULL;
    END;

    outcome := 'created';
    certificate_id := v_certificate_id;
    certificate_created := true;
    event_status := v_status;
    event_id := v_event_id;
    RETURN NEXT;
END;
$func$;
"""

_RECOMPUTE_FUNCTION = """
CREATE FUNCTION mod_learning.recompute_completion_v1(
    p_tenant_id pg_catalog.uuid,
    p_person_id pg_catalog.uuid,
    p_course_id pg_catalog.uuid,
    p_now pg_catalog.timestamptz
)
RETURNS TABLE (
    completion_id pg_catalog.uuid,
    first_completion pg_catalog.bool,
    certificate_id pg_catalog.uuid,
    certificate_created pg_catalog.bool,
    cert_event_status pg_catalog.text,
    cert_event_id pg_catalog.uuid
)
LANGUAGE plpgsql
VOLATILE
SECURITY INVOKER
SET search_path = pg_catalog, pg_temp
AS $func$
#variable_conflict error
DECLARE
    v_completion_id pg_catalog.uuid;
    v_prev_completed_at pg_catalog.timestamptz;
    v_total pg_catalog.int8;
    v_passed_count pg_catalog.int8;
    v_fraction_req pg_catalog.float8;
    v_auto_issue pg_catalog.bool;
    v_pct pg_catalog.float8;
    v_is_complete pg_catalog.bool;
    v_first pg_catalog.bool;
    v_new_status pg_catalog.text;
    v_new_completed_at pg_catalog.timestamptz;
    v_certificate_id pg_catalog.uuid := NULL;
    v_certificate_created pg_catalog.bool := false;
    v_cert_event_status pg_catalog.text := NULL;
    v_cert_event_id pg_catalog.uuid := NULL;
    v_issue_outcome pg_catalog.text;
BEGIN
    IF p_tenant_id IS NULL OR p_person_id IS NULL OR p_course_id IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '22004',
            MESSAGE = 'completion arguments must not be null';
    END IF;

    INSERT INTO mod_learning.course_completions
        (id, tenant_id, person_id, course_id, pct, status)
    VALUES (
        pg_catalog.gen_random_uuid(), p_tenant_id, p_person_id,
        p_course_id, 0.0, 'in_progress'
    )
    ON CONFLICT ON CONSTRAINT uq_course_completions_person_course DO NOTHING;

    SELECT comp.id, comp.completed_at
    INTO v_completion_id, v_prev_completed_at
    FROM mod_learning.course_completions AS comp
    WHERE comp.tenant_id = p_tenant_id
        AND comp.person_id = p_person_id
        AND comp.course_id = p_course_id
    FOR UPDATE;

    SELECT pg_catalog.count(*) INTO v_total
    FROM mod_learning.activities AS act
    WHERE act.tenant_id = p_tenant_id AND act.course_id = p_course_id;

    WITH joined AS (
        SELECT act.id AS act_id,
            act.scoring_policy AS policy,
            scr.passed AS s_passed,
            scr.fraction AS s_fraction,
            scr.source AS s_source,
            scr.created_at AS s_created,
            sub.attempt_no AS s_attempt,
            scr.id AS s_id
        FROM mod_learning.activities AS act
        JOIN mod_learning.submissions AS sub
            ON sub.activity_id = act.id AND sub.tenant_id = act.tenant_id
        JOIN mod_learning.scores AS scr
            ON scr.submission_id = sub.id AND scr.tenant_id = sub.tenant_id
        WHERE act.tenant_id = p_tenant_id
            AND act.course_id = p_course_id
            AND sub.person_id = p_person_id
    ),
    winners AS (
        SELECT ranked.act_id, ranked.s_passed FROM (
            SELECT j.act_id, j.s_passed,
                pg_catalog.row_number() OVER (
                    PARTITION BY j.act_id
                    ORDER BY j.s_attempt ASC, j.s_created ASC, j.s_id ASC
                ) AS rn
            FROM joined AS j WHERE j.policy = 'first'
        ) AS ranked WHERE ranked.rn = 1
        UNION ALL
        SELECT ranked.act_id, ranked.s_passed FROM (
            SELECT j.act_id, j.s_passed,
                pg_catalog.row_number() OVER (
                    PARTITION BY j.act_id
                    ORDER BY j.s_attempt DESC, j.s_created DESC, j.s_id DESC
                ) AS rn
            FROM joined AS j WHERE j.policy = 'latest'
        ) AS ranked WHERE ranked.rn = 1
        UNION ALL
        SELECT ranked.act_id, ranked.s_passed FROM (
            SELECT j.act_id, j.s_passed,
                pg_catalog.row_number() OVER (
                    PARTITION BY j.act_id
                    ORDER BY j.s_fraction DESC, j.s_passed DESC,
                        (j.s_source = 'override') DESC, j.s_created DESC,
                        j.s_attempt ASC, j.s_id ASC
                ) AS rn
            FROM joined AS j WHERE j.policy NOT IN ('first', 'latest')
        ) AS ranked WHERE ranked.rn = 1
    )
    SELECT pg_catalog.count(*) INTO v_passed_count
    FROM winners AS win WHERE win.s_passed IS TRUE;

    SELECT crs.completion_fraction, crs.certificate_auto_issue
    INTO v_fraction_req, v_auto_issue
    FROM mod_learning.courses AS crs
    WHERE crs.tenant_id = p_tenant_id AND crs.id = p_course_id;
    IF NOT FOUND THEN
        v_fraction_req := 1.0;
        v_auto_issue := true;
    END IF;

    v_pct := CASE WHEN v_total > 0
        THEN v_passed_count::pg_catalog.float8 / v_total::pg_catalog.float8
        ELSE 0.0 END;
    v_is_complete := v_total > 0
        AND v_passed_count::pg_catalog.float8 / v_total::pg_catalog.float8
            >= v_fraction_req;
    v_first := v_is_complete AND v_prev_completed_at IS NULL;
    IF v_is_complete THEN
        v_new_status := 'completed';
        v_new_completed_at := COALESCE(
            v_prev_completed_at, p_now, pg_catalog.clock_timestamp()
        );
    ELSE
        v_new_status := 'in_progress';
        v_new_completed_at := NULL;
    END IF;

    UPDATE mod_learning.course_completions AS comp
    SET pct = v_pct,
        status = v_new_status,
        completed_at = v_new_completed_at,
        updated_at = pg_catalog.clock_timestamp()
    WHERE comp.id = v_completion_id;

    IF v_first AND v_auto_issue THEN
        SELECT iss.outcome, iss.certificate_id, iss.certificate_created,
            iss.event_status, iss.event_id
        INTO v_issue_outcome, v_certificate_id, v_certificate_created,
            v_cert_event_status, v_cert_event_id
        FROM mod_learning.issue_certificate_v1(
            p_tenant_id, p_person_id, p_course_id, p_now
        ) AS iss;
        IF v_issue_outcome = 'not_eligible' THEN
            v_certificate_id := NULL;
            v_certificate_created := false;
            v_cert_event_status := NULL;
            v_cert_event_id := NULL;
        END IF;
    END IF;

    completion_id := v_completion_id;
    first_completion := v_first;
    certificate_id := v_certificate_id;
    certificate_created := v_certificate_created;
    cert_event_status := v_cert_event_status;
    cert_event_id := v_cert_event_id;
    RETURN NEXT;
END;
$func$;
"""


_LAB_FUNCTION = """
CREATE OR REPLACE FUNCTION mod_learning.record_lab_result_v1(
    p_tenant_id pg_catalog.uuid,
    p_person_id pg_catalog.uuid,
    p_activity_id pg_catalog.uuid,
    p_instance_id pg_catalog.uuid,
    p_seed pg_catalog.jsonb,
    p_score pg_catalog.float8,
    p_max_score pg_catalog.float8,
    p_fraction pg_catalog.float8,
    p_passed pg_catalog.bool,
    p_per_item pg_catalog.jsonb
)
RETURNS TABLE (
    submission_id pg_catalog.uuid,
    attempt_no pg_catalog.int4,
    score_id pg_catalog.uuid,
    completion_id pg_catalog.uuid,
    first_completion pg_catalog.bool,
    certificate_id pg_catalog.uuid,
    certificate_created pg_catalog.bool,
    course_id pg_catalog.uuid,
    prior_pass_exists pg_catalog.bool,
    event_kinds pg_catalog.text[],
    event_statuses pg_catalog.text[],
    event_ids pg_catalog.uuid[]
)
LANGUAGE plpgsql
VOLATILE
SECURITY INVOKER
SET search_path = pg_catalog, pg_temp
AS $func$
#variable_conflict error
DECLARE
    v_activity_type pg_catalog.text;
    v_course_id pg_catalog.uuid;
    v_attempt_no pg_catalog.int4;
    v_submission_id pg_catalog.uuid;
    v_score_id pg_catalog.uuid;
    v_i pg_catalog.int4;
    v_kind pg_catalog.text;
    v_subject pg_catalog.uuid;
    v_detail pg_catalog.jsonb;
    v_kinds pg_catalog.text[] := ARRAY[]::pg_catalog.text[];
    v_statuses pg_catalog.text[] := ARRAY[]::pg_catalog.text[];
    v_ids pg_catalog.uuid[] := ARRAY[]::pg_catalog.uuid[];
    v_event_id pg_catalog.uuid;
    v_status pg_catalog.text;
    v_party_type pg_catalog.text;
    v_event_kinds3 pg_catalog.text[];
    v_event_subjects pg_catalog.uuid[];
    v_event_details pg_catalog.jsonb[];
    v_completion_id pg_catalog.uuid;
    v_first pg_catalog.bool;
    v_certificate_id pg_catalog.uuid := NULL;
    v_certificate_created pg_catalog.bool := false;
    v_cert_event_status pg_catalog.text := NULL;
    v_cert_event_id pg_catalog.uuid := NULL;
    v_prior pg_catalog.bool;
BEGIN
    IF p_tenant_id IS NULL OR p_person_id IS NULL OR p_activity_id IS NULL
        OR p_instance_id IS NULL OR p_seed IS NULL OR p_score IS NULL
        OR p_max_score IS NULL OR p_fraction IS NULL OR p_passed IS NULL
        OR p_per_item IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '22004',
            MESSAGE = 'lab result arguments must not be null';
    END IF;
    IF pg_catalog.jsonb_typeof(p_seed) <> 'object' THEN
        RAISE EXCEPTION USING ERRCODE = '22023',
            MESSAGE = 'lab result seed must be a JSON object';
    END IF;
    IF pg_catalog.jsonb_typeof(p_per_item) <> 'array' THEN
        RAISE EXCEPTION USING ERRCODE = '22023',
            MESSAGE = 'lab result per_item must be an array of JSON objects';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_catalog.jsonb_array_elements(p_per_item) AS elem
        WHERE pg_catalog.jsonb_typeof(elem) <> 'object'
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '22023',
            MESSAGE = 'lab result per_item must be an array of JSON objects';
    END IF;

    SELECT act.type, act.course_id
    INTO v_activity_type, v_course_id
    FROM mod_learning.activities AS act
    WHERE act.tenant_id = p_tenant_id AND act.id = p_activity_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = '22023',
            MESSAGE = 'activity not found for tenant';
    END IF;
    IF v_activity_type <> 'lab' THEN
        RAISE EXCEPTION USING ERRCODE = '22023',
            MESSAGE = 'lab results require a lab activity';
    END IF;

    SELECT COALESCE(pg_catalog.max(sub.attempt_no), 0) + 1
    INTO v_attempt_no
    FROM mod_learning.submissions AS sub
    WHERE sub.tenant_id = p_tenant_id
        AND sub.activity_id = p_activity_id
        AND sub.person_id = p_person_id;

    INSERT INTO mod_learning.submissions
        (id, tenant_id, activity_id, person_id, answers, attempt_no)
    VALUES (
        pg_catalog.gen_random_uuid(), p_tenant_id, p_activity_id, p_person_id,
        pg_catalog.jsonb_build_object(
            'seed', p_seed, 'instance', p_instance_id::pg_catalog.text
        ),
        v_attempt_no
    )
    RETURNING id INTO v_submission_id;

    INSERT INTO mod_learning.scores
        (id, tenant_id, submission_id, score, max_score, fraction, passed,
         per_item, source, override_reason)
    VALUES (
        pg_catalog.gen_random_uuid(), p_tenant_id, v_submission_id,
        p_score, p_max_score, p_fraction, p_passed, p_per_item, 'auto', NULL
    )
    RETURNING id INTO v_score_id;

    v_event_kinds3 := ARRAY[
        'submission_made',
        'work_graded',
        CASE WHEN p_passed THEN 'lab_check_passed' ELSE 'lab_check_failed' END
    ]::pg_catalog.text[];
    v_event_subjects := ARRAY[
        v_submission_id, v_submission_id, p_activity_id
    ]::pg_catalog.uuid[];
    v_event_details := ARRAY[
        pg_catalog.jsonb_build_object(
            'activity_id', p_activity_id::pg_catalog.text,
            'attempt_no', v_attempt_no,
            'lab', true
        ),
        pg_catalog.jsonb_build_object(
            'activity_id', p_activity_id::pg_catalog.text,
            'passed', p_passed,
            'fraction', p_fraction,
            'lab', true
        ),
        pg_catalog.jsonb_build_object(
            'instance', p_instance_id::pg_catalog.text,
            'checks', pg_catalog.jsonb_array_length(p_per_item)
        )
    ]::pg_catalog.jsonb[];

    FOR v_i IN 1..3 LOOP
        v_kind := v_event_kinds3[v_i];
        v_subject := v_event_subjects[v_i];
        v_detail := v_event_details[v_i];
        v_event_id := NULL;
        v_status := 'recorded';
        BEGIN
            SELECT party.party_type::pg_catalog.text INTO v_party_type
            FROM public.parties AS party
            WHERE party.tenant_id = p_tenant_id AND party.id = p_person_id;
            IF v_party_type IS DISTINCT FROM 'person' THEN
                RAISE EXCEPTION USING ERRCODE = '22023',
                    MESSAGE = 'learning event identity must be a person';
            END IF;
            INSERT INTO mod_learning.learning_events
                (id, tenant_id, person_id, course_id, kind, subject_id,
                 detail, occurred_at)
            VALUES (
                pg_catalog.gen_random_uuid(), p_tenant_id, p_person_id,
                v_course_id, v_kind, v_subject, v_detail,
                pg_catalog.clock_timestamp()
            )
            RETURNING id INTO v_event_id;
        EXCEPTION WHEN OTHERS THEN
            v_status := 'failed';
            v_event_id := NULL;
        END;
        v_kinds := v_kinds || v_kind;
        v_statuses := v_statuses || v_status;
        v_ids := v_ids || v_event_id;
    END LOOP;

    SELECT rc.completion_id, rc.first_completion, rc.certificate_id,
        rc.certificate_created, rc.cert_event_status, rc.cert_event_id
    INTO v_completion_id, v_first, v_certificate_id, v_certificate_created,
        v_cert_event_status, v_cert_event_id
    FROM mod_learning.recompute_completion_v1(
        p_tenant_id, p_person_id, v_course_id, NULL
    ) AS rc;

    IF v_certificate_created THEN
        v_kinds := v_kinds || 'certificate_earned'::pg_catalog.text;
        v_statuses := v_statuses || v_cert_event_status;
        v_ids := v_ids || v_cert_event_id;
    END IF;

    SELECT EXISTS (
        SELECT 1
        FROM mod_learning.scores AS scr
        JOIN mod_learning.submissions AS sub
            ON sub.id = scr.submission_id AND sub.tenant_id = scr.tenant_id
        WHERE scr.tenant_id = p_tenant_id
            AND sub.activity_id = p_activity_id
            AND sub.person_id = p_person_id
            AND scr.passed IS TRUE
            AND scr.id <> v_score_id
    ) INTO v_prior;

    submission_id := v_submission_id;
    attempt_no := v_attempt_no;
    score_id := v_score_id;
    completion_id := v_completion_id;
    first_completion := v_first;
    certificate_id := v_certificate_id;
    certificate_created := v_certificate_created;
    course_id := v_course_id;
    prior_pass_exists := v_prior;
    event_kinds := v_kinds;
    event_statuses := v_statuses;
    event_ids := v_ids;
    RETURN NEXT;
END;
$func$;
"""


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.execute(_ISSUE_FUNCTION)
    op.execute(f"REVOKE ALL ON FUNCTION {_ISSUE_SIGNATURE} FROM PUBLIC")
    op.execute(_RECOMPUTE_FUNCTION)
    op.execute(f"REVOKE ALL ON FUNCTION {_RECOMPUTE_SIGNATURE} FROM PUBLIC")
    op.execute(_LAB_FUNCTION)


def downgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    # Restore the exact lr_0011 body (that released file never changes).
    from dotmac_learning.migrations.versions.lr_0011_lab_result_capability import (
        _FUNCTION as _LR0011_FUNCTION,
    )

    op.execute(
        _LR0011_FUNCTION.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)
    )
    op.execute(f"DROP FUNCTION {_RECOMPUTE_SIGNATURE}")
    op.execute(f"DROP FUNCTION {_ISSUE_SIGNATURE}")
