"""Opening two-person approval becomes a per-window policy (D-9 and R4b).

Before this revision every opening approval was compulsory two-person: a held
partition admission (bi_0013) and the resolution of an opening variance or
control gap (bi_0014, ``opening_approval_ok``) each needed an Approvals request
and a checker who is not the maker. The product now decides, per tenant and at
provision, whether that applies. The decision is a typed value, ``dual_control``
(``'on'`` or ``'off'``), inside the immutable opening-window evidence
(``opening_windows.evidence -> 'window' -> 'dual_control'``). The window guard
already freezes the whole evidence on UPDATE, so the policy cannot change after
provision; the INSERT branch now requires the key to be exactly ``'on'`` or
``'off'``. No value is hard-coded here: the policy is data in the window.

* A window provisioned before this revision has no key and is read as ``'on'``
  (every guard below uses ``coalesce(..., 'on')``), so nothing relaxes silently
  and the ON path is exactly the previous predicate.
* ``'on'``: unchanged, plus a hold admission now explicitly requires a non-null
  approval request id (the column is nullable from here).
* ``'off'``: no approval is applicable and a supplied one is REFUSED rather than
  ignored. A hold admission must carry ``approval_request_id IS NULL``,
  ``approver_refs = []``, ``evidence.approval = null`` and
  ``evidence.dual_control = 'off'``. A variance or control-gap resolution must
  carry a null request id and an empty (never NULL) ``approver_refs`` array.
  Every other check stays: the reviewed disposition and digest, a non-empty
  maker, the maker as actor, the restricted principal, the exact receipt and
  window binding, the closure checks and the mandatory deferred audit row.

Additive and data-free: one column loses NOT NULL, one helper function is added
per plane, and three existing guard functions are replaced. The two variance and
gap guards are rewritten from their stored definition by replacing exactly one
occurrence of the ``opening_approval_ok(...)`` call (the migration refuses to
continue if the text is not found exactly once), so the rest of each guard is
byte-identical to bi_0014. No downgrade: custody cannot be reopened.
"""

from __future__ import annotations

# All identifiers are closed Billing plane/table literals.
# ruff: noqa: S608
from collections.abc import Iterator

from dotmac_kernel.planes import ModulePlane, selected_module_planes

from alembic import op

revision = "bi_0019_opening_dual_control"
down_revision = "bi_0018_account_terms"
branch_labels = None
depends_on = None

_OLD_APPROVAL = (
    "mod_billing.opening_approval_ok(NEW.maker_ref, NEW.approver_refs, "
    "NEW.approval_request_id)"
)
_OLD_PROVISION = "OR nullif(trim(NEW.provision_authority_ref),'') IS NULL"


def _planes() -> Iterator[bool]:
    planes = selected_module_planes("billing")
    for plane in (ModulePlane.TENANT, ModulePlane.PLATFORM):
        if plane in planes:
            yield plane is ModulePlane.PLATFORM


def _upgrade_plane(platform: bool) -> None:
    p = "platform_" if platform else ""
    win_scope = "" if platform else " AND w.tenant_id = tenant"
    param = "" if platform else "tenant uuid, "
    held_args = "NEW.tenant_id, " if not platform else ""
    new_scope = "" if platform else " AND x.tenant_id = NEW.tenant_id"
    approval_call = (
        f"mod_billing.{p}opening_resolution_approval_ok("
        f"{held_args}NEW.window_id, NEW.maker_ref, NEW.approver_refs, "
        "NEW.approval_request_id)"
    )
    provision_check = (
        f"{_OLD_PROVISION}\n"
        "          OR NEW.evidence::jsonb->'window'->>'dual_control' IS NULL\n"
        "          OR NEW.evidence::jsonb->'window'->>'dual_control' NOT IN ('on','off')"
    )
    op.execute(
        f"ALTER TABLE mod_billing.{p}opening_hold_admissions "
        "ALTER COLUMN approval_request_id DROP NOT NULL;"
    )
    op.execute(f"""
    -- The window's policy, read from its immutable evidence. A missing window or
    -- key means 'on'. A resolution carries an approval under 'on' (the previous
    -- predicate, unchanged) and carries none under 'off'.
    CREATE FUNCTION mod_billing.{p}opening_resolution_approval_ok(
      {param}win uuid, maker text, approvers jsonb, request uuid) RETURNS boolean
    LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path = pg_catalog AS $$
    DECLARE dual text;
    BEGIN
      SELECT coalesce(w.evidence::jsonb->'window'->>'dual_control', 'on') INTO dual
        FROM mod_billing.{p}opening_windows w WHERE w.id = win{win_scope};
      dual := coalesce(dual, 'on');
      IF dual = 'on' THEN
        RETURN mod_billing.opening_approval_ok(maker, approvers, request);
      ELSIF dual = 'off' THEN
        RETURN coalesce(nullif(trim(maker), '') IS NOT NULL AND request IS NULL
          AND jsonb_typeof(approvers) = 'array' AND jsonb_array_length(approvers) = 0, false);
      END IF;
      RETURN false;
    END $$;
    """)  # nosec B608 -- closed plane identifiers
    # Each stored guard is rewritten by replacing exactly ONE occurrence of the
    # text below; any other count aborts the migration (fail closed).
    op.execute(f"""
    DO $rewrite$
    DECLARE spec record; def text; fixed text;
    BEGIN
      FOR spec IN SELECT * FROM (VALUES
        ('mod_billing.guard_{p}opening_window()', $o${_OLD_PROVISION}$o$, $n${provision_check}$n$),
        ('mod_billing.guard_{p}opening_variance_event()', $o${_OLD_APPROVAL}$o$, $n${approval_call}$n$),
        ('mod_billing.guard_{p}opening_gap_event()', $o${_OLD_APPROVAL}$o$, $n${approval_call}$n$)
      ) AS t(signature, old_text, new_text)
      LOOP
        def := pg_catalog.pg_get_functiondef(spec.signature::pg_catalog.regprocedure);
        IF (pg_catalog.length(def) - pg_catalog.length(pg_catalog.replace(def, spec.old_text, '')))
            <> pg_catalog.length(spec.old_text)
        THEN RAISE EXCEPTION 'bi_0019: expected exactly one occurrence in %', spec.signature; END IF;
        fixed := pg_catalog.replace(def, spec.old_text, spec.new_text);
        EXECUTE fixed;
      END LOOP;
    END $rewrite$;
    """)  # nosec B608 -- closed plane identifiers and fixed replacement text
    op.execute(f"""
    CREATE OR REPLACE FUNCTION mod_billing.guard_{p}opening_hold_admission() RETURNS trigger
    LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = pg_catalog AS $$
    DECLARE bound uuid; receipt record; refs jsonb; ev jsonb; manifest jsonb; dual text;
    BEGIN
      bound := mod_billing.touch_{p}opening_window_held({held_args}NEW.billing_account_id);
      SELECT * INTO receipt FROM mod_billing.{p}opening_import_receipts x WHERE x.id = NEW.receipt_id{new_scope};
      refs := NEW.approver_refs::jsonb; ev := NEW.evidence::jsonb;
      SELECT w.evidence::jsonb->'window'->'manifest',
             coalesce(w.evidence::jsonb->'window'->>'dual_control', 'on')
        INTO manifest, dual
        FROM mod_billing.{p}opening_windows w WHERE w.id = bound{"" if platform else " AND w.tenant_id = NEW.tenant_id"};
      -- Everything that is not an approval stays mandatory under both policies.
      IF bound <> NEW.window_id OR receipt.id IS NULL OR receipt.receipt_kind <> 'import'
        OR receipt.billing_account_id <> NEW.billing_account_id
        OR NEW.disposition_digest !~ '^sha256:[0-9a-f]{{64}}$'
        OR nullif(trim(NEW.maker_ref),'') IS NULL
        OR ev->>'maker_ref' IS DISTINCT FROM NEW.maker_ref
        OR ev->>'receipt_id' IS DISTINCT FROM NEW.receipt_id::text
        OR ev->>'billing_account_id' IS DISTINCT FROM NEW.billing_account_id::text
        OR ev->>'window_id' IS DISTINCT FROM NEW.window_id::text
        OR ev->'disposition'->>'disposition_digest' IS DISTINCT FROM NEW.disposition_digest
        OR dual IS NULL OR dual NOT IN ('on','off')
      THEN RAISE EXCEPTION 'hold admission requires exact receipt, disposition and maker-checker evidence' USING ERRCODE = '23514'; END IF;
      IF dual = 'on' THEN
        IF NEW.approval_request_id IS NULL
          OR jsonb_typeof(refs) IS DISTINCT FROM 'array' OR jsonb_array_length(refs) = 0
          OR refs ? NEW.maker_ref
          OR ev->'approval'->>'requester_ref' IS DISTINCT FROM NEW.maker_ref
          OR ev->'approval'->'approver_refs' IS DISTINCT FROM refs
          OR ev->'approval'->>'request_id' IS DISTINCT FROM NEW.approval_request_id::text
          OR (ev ? 'dual_control' AND ev->>'dual_control' IS DISTINCT FROM 'on')
        THEN RAISE EXCEPTION 'hold admission requires exact receipt, disposition and maker-checker evidence' USING ERRCODE = '23514'; END IF;
      ELSE
        IF NEW.approval_request_id IS NOT NULL
          OR jsonb_typeof(refs) IS DISTINCT FROM 'array' OR jsonb_array_length(refs) <> 0
          OR ev->'approval' IS DISTINCT FROM 'null'::jsonb
          OR ev->>'dual_control' IS DISTINCT FROM 'off'
        THEN RAISE EXCEPTION 'hold admission under dual_control off carries no approval and records it' USING ERRCODE = '23514'; END IF;
      END IF;
      -- ST-4c: the admitted partition closes exactly like a pre-seal one.
      IF NOT mod_billing.opening_variance_set_matches({"" if platform else "NEW.tenant_id, "}bound, manifest, ARRAY[NEW.billing_account_id::text], false)
        OR mod_billing.opening_closure_defects({"" if platform else "NEW.tenant_id, "}bound, manifest, ARRAY[NEW.billing_account_id::text], false) <> 0
      THEN RAISE EXCEPTION 'held partition does not close: recorded variances differ from the manifest or a control row is not zero' USING ERRCODE = '23514'; END IF;
      RETURN NEW;
    END $$;
    """)  # nosec B608 -- closed plane identifiers and fixed scope expressions


def upgrade() -> None:
    for platform in _planes():
        _upgrade_plane(platform)


def downgrade() -> None:
    raise RuntimeError(
        "Billing opening custody cannot be erased or reopened by downgrade"
    )
