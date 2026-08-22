"""Behaviour canaries for the complete typed network-module suite."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from dotmac_kernel.models import Base, Tenant
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

NOW = datetime(2026, 8, 19, 10, tzinfo=UTC)


@contextmanager
def _module_db(schema: str, models: tuple[type[Base], ...]) -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {schema: None}},
    )
    Base.metadata.create_all(
        engine,
        tables=[Tenant.__table__, *(model.__table__ for model in models)],
    )
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _tenant(db: Session) -> UUID:
    row = Tenant(slug=f"tenant-{uuid4().hex[:10]}", name="Network tenant")
    db.add(row)
    db.flush()
    return row.id


def test_network_inventory_admission_is_idempotent_and_archival_is_terminal() -> None:
    from dotmac_network_inventory import (
        AdmitNode,
        ArchiveNode,
        NodeKind,
        NodeState,
        RegisterInterface,
        RegisterSite,
        admit_node,
        archive_node,
        register_interface,
        register_site,
    )
    from dotmac_network_inventory.models import ALL_MODELS, SCHEMA
    from dotmac_network_inventory.service import NetworkInventoryConflict

    with _module_db(SCHEMA, ALL_MODELS) as db:
        tenant_id = _tenant(db)
        site = register_site(
            db,
            tenant_id=tenant_id,
            command=RegisterSite("abuja-core", "Abuja core", "pop"),
        )
        command = AdmitNode(
            site_id=site.id,
            code="RTR-1",
            name="Core router",
            kind=NodeKind.ROUTER,
            management_identity="router:opaque-1",
            role_codes=("edge", "core", "edge"),
            capability_codes=("bgp",),
            asset_ref="asset:opaque-1",
            source_ref="admission:1",
        )
        admitted = admit_node(db, tenant_id=tenant_id, command=command)
        replay = admit_node(db, tenant_id=tenant_id, command=command)

        assert admitted.created is True
        assert replay.created is False
        assert replay.node.id == admitted.node.id
        assert admitted.node.role_codes == ("core", "edge")
        assert admitted.node.asset_ref == "asset:opaque-1"

        archived = archive_node(
            db,
            tenant_id=tenant_id,
            command=ArchiveNode(
                node_id=admitted.node.id,
                expected=NodeState.ACTIVE,
                reason="retired",
            ),
        )
        assert archived.state is NodeState.ARCHIVED
        with pytest.raises(NetworkInventoryConflict, match="archived"):
            register_interface(
                db,
                tenant_id=tenant_id,
                command=RegisterInterface(
                    node_id=archived.id,
                    name="xe-0/0/0",
                    interface_kind="ethernet",
                ),
            )


def test_observations_deduplicate_and_health_and_alerts_are_evidence_driven() -> None:
    from dotmac_network_observability import (
        AlertState,
        AvailabilityState,
        ObservationKind,
        OpenAlertEvidence,
        RebuildHealth,
        RecordAvailability,
        RecordObservation,
        ResolveAlertEvidence,
        open_alert_evidence,
        rebuild_health,
        record_availability,
        record_observation,
        resolve_alert_evidence,
    )
    from dotmac_network_observability.models import ALL_MODELS, SCHEMA

    with _module_db(SCHEMA, ALL_MODELS) as db:
        tenant_id = _tenant(db)
        observation_command = RecordObservation(
            subject_ref="node:opaque-1",
            kind=ObservationKind.REACHABILITY,
            source_ref="collector:1",
            observed_at=NOW,
            fingerprint="obs-fingerprint-1",
        )
        first = record_observation(db, tenant_id=tenant_id, command=observation_command)
        replay = record_observation(
            db, tenant_id=tenant_id, command=observation_command
        )
        assert first.duplicate is False
        assert replay.duplicate is True
        assert replay.id == first.id

        record_availability(
            db,
            tenant_id=tenant_id,
            command=RecordAvailability(
                subject_ref="node:opaque-1",
                state=AvailabilityState.DOWN,
                source_ref="availability:1",
                observed_at=NOW,
                reason_code="probe-timeout",
            ),
        )
        health = rebuild_health(
            db,
            tenant_id=tenant_id,
            command=RebuildHealth(
                subject_ref="node:opaque-1",
                as_of=NOW,
                source_observation_ids=(first.id,),
            ),
        )
        assert health.state is AvailabilityState.DOWN
        assert health.source_observation_ids == (first.id,)

        alert_command = OpenAlertEvidence(
            subject_ref="node:opaque-1",
            rule_ref="reachability-down",
            severity="critical",
            evidence_ref="evidence:1",
            observed_at=NOW,
        )
        alert = open_alert_evidence(db, tenant_id=tenant_id, command=alert_command)
        alert_replay = open_alert_evidence(
            db, tenant_id=tenant_id, command=alert_command
        )
        assert alert_replay.id == alert.id

        resolution = ResolveAlertEvidence(
            alert_id=alert.id,
            expected=AlertState.OPEN,
            evidence_ref="resolution:1",
            observed_at=NOW + timedelta(minutes=5),
        )
        resolved = resolve_alert_evidence(db, tenant_id=tenant_id, command=resolution)
        resolved_replay = resolve_alert_evidence(
            db, tenant_id=tenant_id, command=resolution
        )
        assert resolved.state is AlertState.RESOLVED
        assert resolved_replay == resolved


def test_topology_selects_minimum_cost_and_rebuilds_unreachable_projection() -> None:
    from dotmac_network_topology import (
        DeclareLink,
        LinkKind,
        RebuildTopology,
        ResolveForwarding,
        declare_link,
        rebuild_topology,
        resolve_forwarding,
    )
    from dotmac_network_topology.models import ALL_MODELS, SCHEMA

    with _module_db(SCHEMA, ALL_MODELS) as db:
        tenant_id = _tenant(db)

        def link(left: str, right: str, cost: int):
            return declare_link(
                db,
                tenant_id=tenant_id,
                command=DeclareLink(
                    left_ref=left,
                    right_ref=right,
                    kind=LinkKind.LOGICAL,
                    source_ref=f"declared:{left}:{right}",
                    cost=cost,
                ),
            )

        direct = link("A", "B", 10)
        first_hop = link("A", "C", 2)
        second_hop = link("C", "B", 2)
        path = resolve_forwarding(
            db,
            tenant_id=tenant_id,
            command=ResolveForwarding(
                source_ref="A",
                destination_ref="B",
                declared_link_ids=(direct.id, first_hop.id, second_hop.id),
                observed_link_ids=(),
                as_of=NOW,
            ),
        )
        assert path.hop_refs == ("A", "C", "B")
        assert path.total_cost == 4

        report = rebuild_topology(
            db,
            tenant_id=tenant_id,
            command=RebuildTopology(
                projection_ref="network:primary",
                declared_link_ids=(),
                observed_link_ids=(),
                as_of=NOW + timedelta(minutes=1),
            ),
        )
        assert report.changed is True
        assert report.path_count == 1
        assert report.gap_count == 1


def test_assurance_owns_incident_transitions_and_resolution_evidence() -> None:
    from dotmac_network_assurance import (
        AssuranceConflict,
        ImpactSeverity,
        IncidentState,
        OpenIncident,
        ResolveIncident,
        UpdateIncident,
        open_incident,
        resolve_incident,
        update_incident,
    )
    from dotmac_network_assurance.models import ALL_MODELS, SCHEMA

    with _module_db(SCHEMA, ALL_MODELS) as db:
        tenant_id = _tenant(db)
        incident = open_incident(
            db,
            tenant_id=tenant_id,
            command=OpenIncident(
                code="INC-1",
                summary="Core path unavailable",
                severity=ImpactSeverity.MAJOR,
                detected_at=NOW,
                detection_ref="alert:opaque-1",
                source_observation_refs=("observation:opaque-1",),
            ),
        )
        investigating = update_incident(
            db,
            tenant_id=tenant_id,
            command=UpdateIncident(
                incident_id=incident.id,
                expected=IncidentState.OPEN,
                requested=IncidentState.INVESTIGATING,
                evidence_ref="timeline:1",
                note="NOC acknowledged",
            ),
        )
        assert investigating.state is IncidentState.INVESTIGATING
        with pytest.raises(AssuranceConflict, match="transition"):
            update_incident(
                db,
                tenant_id=tenant_id,
                command=UpdateIncident(
                    incident_id=incident.id,
                    expected=IncidentState.OPEN,
                    requested=IncidentState.MONITORING,
                    evidence_ref="timeline:stale",
                ),
            )
        resolved = resolve_incident(
            db,
            tenant_id=tenant_id,
            command=ResolveIncident(
                incident_id=incident.id,
                expected=IncidentState.INVESTIGATING,
                resolution_code="fiber-restored",
                resolution_summary="Continuity restored after splice repair",
                resolved_at=NOW + timedelta(hours=1),
                evidence_ref="repair:opaque-1",
            ),
        )
        assert resolved.state is IncidentState.RESOLVED
        assert resolved.resolution_summary == "Continuity restored after splice repair"


def test_control_command_has_one_approved_dispatch_and_terminal_evidence() -> None:
    from dotmac_network_control import (
        CommandState,
        ExecutionOutcome,
        MarkDispatched,
        RecordExecutionObservation,
        RequestCommand,
        mark_dispatched,
        record_execution_observation,
        request_command,
    )
    from dotmac_network_control.models import ALL_MODELS, SCHEMA

    with _module_db(SCHEMA, ALL_MODELS) as db:
        tenant_id = _tenant(db)
        requested = request_command(
            db,
            tenant_id=tenant_id,
            command=RequestCommand(
                operation_code="disable-port",
                target_ref="port:opaque-1",
                capability_code="network-control.v1",
                parameters=(("reason", "loop"),),
                request_fingerprint="request-fingerprint-1",
                correlation_ref="change:opaque-1",
                requested_by_ref="operator:opaque-1",
                requires_approval=False,
            ),
        )
        assert requested.state is CommandState.APPROVED
        dispatch = mark_dispatched(
            db,
            tenant_id=tenant_id,
            command=MarkDispatched(
                command_id=requested.id,
                expected=CommandState.APPROVED,
                dispatch_ref="dispatch:1",
                plugin_capability="network-control.v1",
                dispatched_at=NOW,
            ),
        )
        observation = RecordExecutionObservation(
            command_id=requested.id,
            dispatch_ref=dispatch.dispatch_ref,
            outcome=ExecutionOutcome.SUCCEEDED,
            observed_at=NOW + timedelta(seconds=10),
            evidence_ref="plugin-result:1",
            result_fingerprint="result-fingerprint-1",
        )
        first = record_execution_observation(
            db, tenant_id=tenant_id, command=observation
        )
        replay = record_execution_observation(
            db, tenant_id=tenant_id, command=observation
        )
        assert first.outcome is ExecutionOutcome.SUCCEEDED
        assert replay.id == first.id


def test_fiber_continuity_uses_canonical_splice_identity() -> None:
    from dotmac_fiber_plant import (
        ContinuityQuery,
        FiberPlantConflict,
        RecordSplice,
        RecordTermination,
        RegisterCable,
        RegisterStrand,
        RegisterStructure,
        StructureKind,
        record_splice,
        record_termination,
        register_cable,
        register_strand,
        register_structure,
        resolve_continuity,
    )
    from dotmac_fiber_plant.models import ALL_MODELS, SCHEMA

    with _module_db(SCHEMA, ALL_MODELS) as db:
        tenant_id = _tenant(db)

        def structure(code: str):
            return register_structure(
                db,
                tenant_id=tenant_id,
                command=RegisterStructure(
                    code=code,
                    name=code,
                    kind=StructureKind.CLOSURE,
                    location_ref=f"location:{code}",
                ),
            )

        left = structure("LEFT")
        middle = structure("MIDDLE")
        right = structure("RIGHT")
        left_cable = register_cable(
            db,
            tenant_id=tenant_id,
            command=RegisterCable(
                code="CABLE-1",
                name="Left feeder",
                strand_count=1,
                start_structure_id=left.id,
                end_structure_id=middle.id,
            ),
        )
        right_cable = register_cable(
            db,
            tenant_id=tenant_id,
            command=RegisterCable(
                code="CABLE-2",
                name="Right feeder",
                strand_count=1,
                start_structure_id=middle.id,
                end_structure_id=right.id,
            ),
        )
        left_strand = register_strand(
            db,
            tenant_id=tenant_id,
            command=RegisterStrand(left_cable.id, 1, "blue"),
        )
        right_strand = register_strand(
            db,
            tenant_id=tenant_id,
            command=RegisterStrand(right_cable.id, 1, "blue"),
        )
        record_termination(
            db,
            tenant_id=tenant_id,
            command=RecordTermination(
                left.id,
                left_strand.id,
                "endpoint:left",
                None,
                "termination:1",
                NOW,
            ),
        )
        record_termination(
            db,
            tenant_id=tenant_id,
            command=RecordTermination(
                right.id,
                right_strand.id,
                "endpoint:right",
                None,
                "termination:2",
                NOW,
            ),
        )
        splice = RecordSplice(
            middle.id,
            left_strand.id,
            right_strand.id,
            Decimal("0.1250"),
            "splice:1",
            NOW,
        )
        record_splice(db, tenant_id=tenant_id, command=splice)
        with pytest.raises(FiberPlantConflict, match="splice pair"):
            record_splice(
                db,
                tenant_id=tenant_id,
                command=RecordSplice(
                    middle.id,
                    right_strand.id,
                    left_strand.id,
                    Decimal("0.1250"),
                    "splice:2",
                    NOW,
                ),
            )
        path = resolve_continuity(
            db,
            tenant_id=tenant_id,
            query=ContinuityQuery("endpoint:left", "endpoint:right", NOW),
        )
        assert path.continuous is True
        assert path.strand_ids == (left_strand.id, right_strand.id)


def test_access_projection_and_accounting_keep_session_evidence() -> None:
    from dotmac_network_access import (
        AccessState,
        AuthenticationOutcome,
        ProjectAccessPolicy,
        ReconcileAccess,
        RecordAccounting,
        RecordAuthentication,
        SessionQuery,
        SessionState,
        project_access_policy,
        query_sessions,
        reconcile_access,
        record_accounting,
        record_authentication,
    )
    from dotmac_network_access.models import ALL_MODELS, SCHEMA

    with _module_db(SCHEMA, ALL_MODELS) as db:
        tenant_id = _tenant(db)
        projection = project_access_policy(
            db,
            tenant_id=tenant_id,
            command=ProjectAccessPolicy(
                subject_ref="service:opaque-1",
                desired_state=AccessState.ENABLED,
                policy_code="standard",
                policy_version="v1",
                attributes=(("rate", "100m"),),
                decision_ref="entitlement:opaque-1",
            ),
        )
        authentication = RecordAuthentication(
            subject_ref=projection.subject_ref,
            nas_ref="nas:opaque-1",
            session_ref="session:1",
            outcome=AuthenticationOutcome.ACCEPTED,
            reason_code=None,
            source_ref="radius-auth:1",
            observed_at=NOW,
            fingerprint="auth-fingerprint-1",
        )
        assert (
            record_authentication(
                db, tenant_id=tenant_id, command=authentication
            ).duplicate
            is False
        )
        assert (
            record_authentication(
                db, tenant_id=tenant_id, command=authentication
            ).duplicate
            is True
        )

        for event_kind, fingerprint, offset, octets in (
            ("start", "acct-fingerprint-1", 0, 0),
            ("stop", "acct-fingerprint-2", 60, 1024),
        ):
            record_accounting(
                db,
                tenant_id=tenant_id,
                command=RecordAccounting(
                    subject_ref=projection.subject_ref,
                    nas_ref="nas:opaque-1",
                    session_ref="session:1",
                    event_kind=event_kind,
                    input_octets=octets,
                    output_octets=octets * 2,
                    session_seconds=offset,
                    source_ref=f"radius-acct:{event_kind}",
                    observed_at=NOW + timedelta(seconds=offset),
                    fingerprint=fingerprint,
                ),
            )
        sessions = query_sessions(
            db,
            tenant_id=tenant_id,
            query=SessionQuery(subject_ref=projection.subject_ref),
        )
        assert len(sessions) == 1
        assert sessions[0].state is SessionState.CLOSED
        assert sessions[0].closed_reason_code == "accounting-stop"
        assert sessions[0].close_source_ref == "radius-acct:stop"

        drift = reconcile_access(
            db,
            tenant_id=tenant_id,
            command=ReconcileAccess(
                subject_ref=projection.subject_ref,
                observed_state=AccessState.ENABLED,
                observed_fingerprint=projection.desired_fingerprint,
                source_ref="access-reader:1",
                observed_at=NOW + timedelta(minutes=2),
            ),
        )
        assert drift.drifted is False


def test_pon_capacity_commissioning_and_desired_state_are_one_lifecycle() -> None:
    from dotmac_pon_access import (
        AdmitOnt,
        AssignOnt,
        CommissionOnt,
        DesiredConfigState,
        OntState,
        PonAccessConflict,
        ReconcilePon,
        RegisterOlt,
        RegisterPonPort,
        SetDesiredService,
        admit_ont,
        assign_ont,
        commission_ont,
        reconcile_pon,
        register_olt,
        register_pon_port,
        set_desired_service,
    )
    from dotmac_pon_access.models import ALL_MODELS, SCHEMA

    with _module_db(SCHEMA, ALL_MODELS) as db:
        tenant_id = _tenant(db)
        olt = register_olt(
            db,
            tenant_id=tenant_id,
            command=RegisterOlt(
                code="OLT-1",
                name="Primary OLT",
                management_ref="node:opaque-1",
                vendor_family="provider-neutral",
                capability_codes=("pon-access.v1",),
            ),
        )
        port = register_pon_port(
            db,
            tenant_id=tenant_id,
            command=RegisterPonPort(olt.id, 0, 1, "0/1", 1),
        )
        ont = admit_ont(
            db,
            tenant_id=tenant_id,
            command=AdmitOnt(
                serial_number="ABCD0001",
                vendor_family="provider-neutral",
                pon_port_id=port.id,
                registration_ref="discovery:1",
                observed_at=NOW,
            ),
        )
        with pytest.raises(PonAccessConflict, match="capacity"):
            admit_ont(
                db,
                tenant_id=tenant_id,
                command=AdmitOnt(
                    serial_number="ABCD0002",
                    vendor_family="provider-neutral",
                    pon_port_id=port.id,
                    registration_ref="discovery:2",
                    observed_at=NOW,
                ),
            )
        assigned = assign_ont(
            db,
            tenant_id=tenant_id,
            command=AssignOnt(
                ont_id=ont.id,
                expected=OntState.ADMITTED,
                service_subject_ref="service:opaque-1",
                assignment_ref="assignment:1",
                assigned_at=NOW,
            ),
        )
        commissioning = CommissionOnt(
            ont_id=assigned.id,
            expected=OntState.ASSIGNED,
            profile_code="standard",
            desired_config_ref="desired:1",
            operation_ref="operation:1",
            commissioned_at=NOW + timedelta(minutes=1),
        )
        first = commission_ont(db, tenant_id=tenant_id, command=commissioning)
        replay = commission_ont(db, tenant_id=tenant_id, command=commissioning)
        assert first.changed is True
        assert replay.changed is False

        desired = set_desired_service(
            db,
            tenant_id=tenant_id,
            command=SetDesiredService(
                ont_id=assigned.id,
                service_ref="service-config:1",
                profile_code="standard",
                vlan_ref="vlan:opaque-1",
                ip_assignment_ref="ip-assignment:opaque-1",
                desired_fingerprint="desired-fingerprint-1",
                decision_ref="service-decision:1",
            ),
        )
        report = reconcile_pon(
            db,
            tenant_id=tenant_id,
            command=ReconcilePon(
                ont_id=assigned.id,
                service_ref=desired.service_ref,
                observed_fingerprint=desired.desired_fingerprint,
                evidence_ref="pon-reader:1",
                observed_at=NOW + timedelta(minutes=2),
            ),
        )
        assert report.drifted is False
        assert report.desired.state is DesiredConfigState.APPLIED
