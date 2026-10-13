"""Canonical sequencing adapter. No default backend, custody access or live CLI.

The runnable packet pins this adapter and the hosted-tested helpers. Its caller
supplies reviewed objects bound to one session, manifest and proven-owned UID.
This module never treats a caller boolean as GitHub or deployment authorization.
"""

import json

REQUIRED_CLEANUP = (
    "temporary_policy_removed",
    "signing_token_revoked",
    "root_revoked",
    "existing_roles_and_CAs_unchanged",
    "cleanup_completed_before_matrix",
)


def run_b8(
    *,
    client,
    transport,
    owned_identity,
    issuer,
    write_execution_deadline,
    principal_input,
):
    """Custody readiness and run-specific packet approval precede this call.

    Issuer uses the reviewed direct route to Observe and the remaining client
    budget. Any stage failure ends the campaign; finally performs only cleanup.
    Unknown closure deliberately retains the locked UID and ownership evidence.
    """
    result = None
    cleanup_receipt = None
    try:
        transport.initialize_closed()
        client.initialize_closed()
        requests = client.prepare(principal_input, seconds=240)
        issued = issuer(requests, remaining_seconds=client.remaining())
        public = issued["public_record"]
        removed = public.get("temporary_roles_removed")
        if (
            not all(public.get(k) is True for k in REQUIRED_CLEANUP)
            or type(removed) is not int
            or removed != 4
            or type(public.get("certificate_count")) is not int
            or public["certificate_count"] != 7
            or issued.get("breakglass_closed") is not True
        ):
            raise RuntimeError("issuer_cleanup_unproved")
        client.install_certificates(
            issued["certificates"], valid_until_epoch=issued["valid_until_epoch"]
        )
        issued = None
        transport.open()
        # Qualification runs inside the pinned nonroot worker after transport
        # opens. It shares this absolute deadline; no separate root probe.
        write_execution_deadline(
            client,
            json.dumps(
                {"execution_deadline_ns": client.window.execution_deadline_ns},
                sort_keys=True,
                separators=(",", ":"),
            ).encode(),
        )
        result = client.execute()
    finally:
        # Containment precedes stopping the worker; a first cleanup may retain
        # its UID guard until client termination proves no process can use it.
        try:
            transport.contain()
        finally:
            try:
                cleanup_receipt = client.cleanup()
            finally:
                try:
                    transport.cleanup()
                finally:
                    owned_identity.cleanup()
    if (
        type(cleanup_receipt) is not dict
        or set(cleanup_receipt) != {"resource_closed", "material_cap"}
        or cleanup_receipt["resource_closed"] is not True
        or cleanup_receipt["material_cap"] != "ATTESTED"
    ):
        raise RuntimeError("client_material_cap_unproven")
    return {
        "matrix_result": result,
        "full_b8_complete": False,
        "admission_changed": False,
    }
