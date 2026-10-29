"""Freeze receipt dispatch identity before network; leave legacy history unknown.

Adoption requires quiescing old receipt workers. There is deliberately no
column default and no backfill, even for zero-attempt historical receipts.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "ig_0018_dispatch_snapshot"
down_revision = "ig_0017_provisioning"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "inbox_receipts",
        sa.Column(
            "delivery_snapshot_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        schema="mod_intg",
    )
    op.execute("""
        CREATE FUNCTION mod_intg.valid_dispatch_snapshot(value jsonb)
        RETURNS boolean LANGUAGE plpgsql IMMUTABLE AS $$
        DECLARE d jsonb; p jsonb; s jsonb; c jsonb; k text;
        BEGIN
            IF value IS NULL THEN RETURN true; END IF;
            IF jsonb_typeof(value) <> 'object' OR
               jsonb_typeof(value->'schema_version') IS DISTINCT FROM 'number' OR
               value->>'schema_version' IS DISTINCT FROM '1' THEN RETURN false; END IF;
            IF value->>'state' = 'unstarted' THEN
                RETURN value = '{"schema_version": 1,"state":"unstarted"}'::jsonb;
            END IF;
            IF value->>'state' IS DISTINCT FROM 'frozen' OR
               (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(value) key)
               <> ARRAY['capability','destination','schema_version','source','state'] THEN RETURN false; END IF;
            c := value->'capability';
            IF jsonb_typeof(c) IS DISTINCT FROM 'object' THEN RETURN false; END IF;
            IF (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(c) key)
               <> ARRAY['application','capability_id','contract','owner_module','summary']
               OR jsonb_typeof(c->'contract') IS DISTINCT FROM 'object' THEN RETURN false; END IF;
            FOREACH k IN ARRAY ARRAY['application','capability_id','owner_module','summary'] LOOP
                IF jsonb_typeof(c->k) IS DISTINCT FROM 'string' OR
                   length(trim(c->>k)) = 0 OR c->>k <> trim(c->>k) THEN RETURN false; END IF;
            END LOOP;
            IF (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(c->'contract') key)
               <> ARRAY['command_schema','contract_digest','deprecation','observation_schema',
                        'result_schema','schema_grace'] THEN RETURN false; END IF;
            FOREACH k IN ARRAY ARRAY['command_schema','result_schema','observation_schema',
                                     'deprecation','schema_grace'] LOOP
                IF jsonb_typeof(c->'contract'->k) NOT IN ('object','null') THEN RETURN false; END IF;
            END LOOP;
            -- CapabilityContract has two exclusive owner-declared alternatives:
            -- published schemas with their digest, or explicit grace with no
            -- schemas and no digest. Null must never become invented agreement.
            IF c->'contract'->'command_schema' <> 'null'::jsonb
               OR c->'contract'->'result_schema' <> 'null'::jsonb
               OR c->'contract'->'observation_schema' <> 'null'::jsonb THEN
                IF c->'contract'->'schema_grace' IS DISTINCT FROM 'null'::jsonb
                   OR jsonb_typeof(c->'contract'->'contract_digest') IS DISTINCT FROM 'string'
                   OR c->'contract'->>'contract_digest' !~ '^[0-9a-f]{64}$' THEN RETURN false; END IF;
            ELSE
                IF jsonb_typeof(c->'contract'->'schema_grace') IS DISTINCT FROM 'object'
                   OR c->'contract'->'contract_digest' IS DISTINCT FROM 'null'::jsonb
                   THEN RETURN false; END IF;
            END IF;
            s := c->'contract'->'deprecation';
            IF s <> 'null'::jsonb THEN
                IF (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(s) key)
                   <> ARRAY['reason','replaced_by','retire_after'] THEN RETURN false; END IF;
                FOREACH k IN ARRAY ARRAY['reason','replaced_by','retire_after'] LOOP
                    IF jsonb_typeof(s->k) IS DISTINCT FROM 'string' THEN RETURN false; END IF;
                END LOOP;
            END IF;
            s := c->'contract'->'schema_grace';
            IF s <> 'null'::jsonb THEN
                IF (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(s) key)
                   <> ARRAY['reason','retire_after','tracked_by'] THEN RETURN false; END IF;
                FOREACH k IN ARRAY ARRAY['reason','retire_after','tracked_by'] LOOP
                    IF jsonb_typeof(s->k) IS DISTINCT FROM 'string' THEN RETURN false; END IF;
                END LOOP;
            END IF;
            s := value->'source'; d := value->'destination';
            IF jsonb_typeof(s) IS DISTINCT FROM 'object' OR
               jsonb_typeof(d) IS DISTINCT FROM 'object' THEN RETURN false; END IF;
            IF (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(s) key)
               <> ARRAY['connector_key','installation_id'] OR
               (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(d) key)
               <> ARRAY['application','capability_binding_id','capability_id','contract_version',
                        'destination_revision_id','product_port','scope'] THEN RETURN false; END IF;
            IF jsonb_typeof(s->'connector_key') IS DISTINCT FROM 'string' OR
               length(trim(s->>'connector_key')) = 0 OR
               s->>'connector_key' <> trim(s->>'connector_key') OR
               jsonb_typeof(s->'installation_id') IS DISTINCT FROM 'string' OR
               (s->>'installation_id') !~ '^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$'
               THEN RETURN false; END IF;
            FOREACH k IN ARRAY ARRAY['application','capability_binding_id','capability_id','destination_revision_id'] LOOP
                IF jsonb_typeof(d->k) IS DISTINCT FROM 'string' OR
                   length(trim(d->>k)) = 0 OR d->>k <> trim(d->>k) THEN RETURN false; END IF;
            END LOOP;
            IF (d->>'capability_binding_id') !~ '^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$' OR
               (d->>'destination_revision_id') !~ '^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$' OR
               jsonb_typeof(d->'contract_version') IS DISTINCT FROM 'number' OR
               (d->>'contract_version') !~ '^[1-9][0-9]*$' THEN RETURN false; END IF;
            s := d->'scope';
            IF jsonb_typeof(s) IS DISTINCT FROM 'object' THEN RETURN false; END IF;
            IF (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(s) key)
               <> ARRAY['kind','ref'] THEN RETURN false; END IF;
            FOREACH k IN ARRAY ARRAY['kind','ref'] LOOP
                IF jsonb_typeof(s->k) IS DISTINCT FROM 'string' OR
                   length(trim(s->>k)) = 0 OR s->>k <> trim(s->>k) THEN RETURN false; END IF;
            END LOOP;
            IF c->'application' IS DISTINCT FROM d->'application'
               OR c->'capability_id' IS DISTINCT FROM d->'capability_id' THEN RETURN false; END IF;
            p := d->'product_port';
            IF p = 'null'::jsonb THEN RETURN true; END IF;
            IF jsonb_typeof(p) IS DISTINCT FROM 'object' THEN RETURN false; END IF;
            IF p->>'schema_version' NOT IN ('dotmac.io/product-port-descriptor/v1',
                'dotmac.io/product-port-descriptor/v2','dotmac.io/product-port-descriptor/v3')
                OR p->>'schema_version' IS NULL THEN RETURN false; END IF;
            IF p->>'schema_version' = 'dotmac.io/product-port-descriptor/v3' THEN
                IF (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(p) key)
                   <> ARRAY['activation_state','application','capability_contract','capability_id',
                   'capability_summary','contract_version','delivery_path','descriptor_digest',
                   'destination_binding_id','destination_scope','mirror_path','owner_module',
                   'schema_version','source_revision','wire_schema_version'] OR
                   jsonb_typeof(p->'wire_schema_version') IS DISTINCT FROM 'string' OR
                   p->>'wire_schema_version' NOT IN ('dotmac.io/product-observation/v1',
                       'dotmac.io/product-observation/v2','dotmac.io/integrator-observation-envelope/v1') OR
                   p->'capability_contract' IS DISTINCT FROM c->'contract' THEN RETURN false; END IF;
            ELSE
                IF (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(p) key)
                   <> ARRAY['activation_state','application','capability_id','capability_summary',
                   'contract_version','delivery_path','descriptor_digest','destination_binding_id',
                   'destination_scope','mirror_path','owner_module','schema_version','source_revision']
                   THEN RETURN false; END IF;
            END IF;
            FOREACH k IN ARRAY ARRAY['activation_state','application','capability_id','capability_summary',
                'delivery_path','descriptor_digest','destination_binding_id','mirror_path','owner_module',
                'schema_version','source_revision'] LOOP
                IF jsonb_typeof(p->k) IS DISTINCT FROM 'string' OR
                   length(trim(p->>k)) = 0 OR p->>k <> trim(p->>k) THEN RETURN false; END IF;
            END LOOP;
            FOREACH k IN ARRAY ARRAY['delivery_path','mirror_path'] LOOP
                IF left(p->>k,1) <> '/' OR left(p->>k,2) = '//'
                   OR strpos(p->>k,chr(92)) > 0 OR strpos(p->>k,'?') > 0 OR strpos(p->>k,'#') > 0
                   OR ARRAY['.','..'] && string_to_array(p->>k,'/') THEN RETURN false; END IF;
            END LOOP;
            RETURN p->'contract_version' = d->'contract_version'
               AND p->'application' = d->'application' AND p->'capability_id' = d->'capability_id'
               AND p->'destination_scope' = d->'scope'
               AND p->'owner_module' = c->'owner_module'
               AND p->'capability_summary' = c->'summary'
               AND p->>'activation_state' IN ('configured_disabled','enabled','quarantined','retired')
               AND (p->>'destination_binding_id') ~ '^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$'
               AND (p->>'source_revision') ~ '^[0-9a-f]{64}$'
               AND (p->>'descriptor_digest') ~ '^[0-9a-f]{64}$'
               AND left(p->>'delivery_path',1) = '/' AND left(p->>'mirror_path',1) = '/';
        END;
        $$;
    """)
    op.create_check_constraint(
        "ck_inbox_receipts_dispatch_snapshot",
        "inbox_receipts",
        "mod_intg.valid_dispatch_snapshot(delivery_snapshot_json) IS TRUE",
        schema="mod_intg",
    )
    op.execute("""
        CREATE FUNCTION mod_intg.guard_receipt_dispatch_identity()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.delivery_snapshot_json IS NOT NULL AND
                   NEW.delivery_snapshot_json IS DISTINCT FROM '{"schema_version": 1,"state":"unstarted"}'::jsonb THEN
                    RAISE EXCEPTION 'new dispatch must be explicitly unstarted';
                END IF;
                RETURN NEW;
            END IF;
            IF OLD.delivery_snapshot_json IS NULL THEN
                IF NEW.delivery_snapshot_json IS NOT NULL THEN
                    RAISE EXCEPTION 'unknown dispatch history cannot be promoted';
                END IF;
            ELSIF OLD.delivery_snapshot_json->>'state' = 'frozen' THEN
                IF ROW(OLD.id, OLD.installation_id, OLD.capability_binding_id,
                       OLD.provider_event_id, OLD.event_type, OLD.payload_digest,
                       OLD.delivery_snapshot_json, OLD.delivery_fingerprint,
                       OLD.delivery_idempotency_key, OLD.correlation_id,
                       OLD.destination_application, OLD.destination_contract_version, OLD.destination_revision_id)
                   IS DISTINCT FROM
                   ROW(NEW.id, NEW.installation_id, NEW.capability_binding_id,
                       NEW.provider_event_id, NEW.event_type, NEW.payload_digest,
                       NEW.delivery_snapshot_json, NEW.delivery_fingerprint,
                       NEW.delivery_idempotency_key, NEW.correlation_id,
                       NEW.destination_application, NEW.destination_contract_version, NEW.destination_revision_id) THEN
                    RAISE EXCEPTION 'receipt dispatch identity is immutable';
                END IF;
                IF OLD.payload_json IS DISTINCT FROM NEW.payload_json AND (
                    NEW.payload_json IS NULL OR (
                        OLD.payload_json IS NOT NULL
                        AND jsonb_typeof(OLD.payload_json->'__dotmac_redacted__') IS DISTINCT FROM 'object'
                        AND jsonb_typeof(NEW.payload_json) = 'object'
                        AND (SELECT count(*) FROM jsonb_object_keys(NEW.payload_json)) = 1
                        AND jsonb_typeof(NEW.payload_json->'__dotmac_redacted__') = 'object'
                        AND NEW.payload_json->'__dotmac_redacted__'->>'payload_digest' = OLD.payload_digest
                    )
                ) IS NOT TRUE THEN
                    RAISE EXCEPTION 'receipt dispatch identity is immutable';
                END IF;
            ELSIF NEW.delivery_snapshot_json IS DISTINCT FROM OLD.delivery_snapshot_json THEN
                IF NEW.delivery_snapshot_json->>'state' IS DISTINCT FROM 'frozen'
                   OR NEW.state <> 'processing' OR NEW.leased_until IS NULL
                   OR OLD.delivery_fingerprint IS NOT NULL OR OLD.delivery_idempotency_key IS NOT NULL
                   OR OLD.destination_application IS NOT NULL OR OLD.destination_contract_version IS NOT NULL
                   OR OLD.destination_revision_id IS NOT NULL
                   OR NEW.delivery_fingerprint IS NULL OR NEW.delivery_idempotency_key IS NULL
                   OR NEW.correlation_id IS NULL OR NEW.destination_application IS NULL
                   OR NEW.destination_contract_version IS NULL OR NEW.destination_revision_id IS NULL
                   OR NEW.delivery_fingerprint !~ '^[0-9a-f]{64}$'
                   OR length(NEW.delivery_idempotency_key) = 0 OR length(NEW.correlation_id) = 0
                   OR NEW.delivery_snapshot_json->'source'->>'installation_id' IS DISTINCT FROM NEW.installation_id::text
                   OR NEW.delivery_snapshot_json->'destination'->>'capability_binding_id' IS DISTINCT FROM NEW.capability_binding_id::text
                   OR NEW.delivery_snapshot_json->'destination'->>'application' IS DISTINCT FROM NEW.destination_application
                   OR NEW.delivery_snapshot_json->'destination'->>'contract_version' IS DISTINCT FROM NEW.destination_contract_version::text
                   OR NEW.delivery_snapshot_json->'destination'->>'destination_revision_id' IS DISTINCT FROM NEW.destination_revision_id::text THEN
                    RAISE EXCEPTION 'invalid first dispatch freeze';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER receipt_dispatch_identity_final
        AFTER INSERT OR UPDATE ON mod_intg.inbox_receipts
        FOR EACH ROW EXECUTE FUNCTION mod_intg.guard_receipt_dispatch_identity();
    """)
    op.execute(
        "REVOKE ALL ON FUNCTION mod_intg.valid_dispatch_snapshot(jsonb) FROM PUBLIC;"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION mod_intg.valid_dispatch_snapshot(jsonb) TO platform_api, app_admin;"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION mod_intg.guard_receipt_dispatch_identity() FROM PUBLIC;"
    )
    op.execute("REVOKE ALL ON mod_intg.inbox_receipts FROM app_user;")


def downgrade() -> None:
    op.execute("LOCK TABLE mod_intg.inbox_receipts IN ACCESS EXCLUSIVE MODE;")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM mod_intg.inbox_receipts
                       WHERE delivery_snapshot_json->>'state' = 'frozen') THEN
                RAISE EXCEPTION 'cannot erase frozen receipt dispatch identity';
            END IF;
        END $$;
    """)
    op.execute(
        "DROP TRIGGER receipt_dispatch_identity_final ON mod_intg.inbox_receipts;"
    )
    op.execute("DROP FUNCTION mod_intg.guard_receipt_dispatch_identity();")
    op.drop_constraint(
        "ck_inbox_receipts_dispatch_snapshot",
        "inbox_receipts",
        type_="check",
        schema="mod_intg",
    )
    op.execute("DROP FUNCTION mod_intg.valid_dispatch_snapshot(jsonb);")
    op.drop_column("inbox_receipts", "delivery_snapshot_json", schema="mod_intg")
