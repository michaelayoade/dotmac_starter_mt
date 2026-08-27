"""A migration that shipped in a published tag is history, and history is bytes.

## The enforceable premise (ADR-0018)

Every migration file present at any tag recorded below is inside a wheel on the
registry, and has therefore RUN, unmodified, in at least one database this
repository does not own and cannot inspect. The owner and tag counts are
derived by the guards; prose does not freeze a census that becomes false with
the next release.

Allocation is the sharper case, and the reason the guard grew a second
distribution rather than staying a one-module special. Its first four releases
hold ONE migration with ONE digest: a2 exposed `versions_dir()`, a3 made the ORM
relationships class-bound, a4 moved a manifest declaration between plane slots.
a6 then shipped the two prerequisite verifier revisions after deliberately
skipping a5. A quiet directory is not evidence its released bytes remain ours.

The integration a3 entry was added while #204 was open: a3 was tagged from `b14f66e`
after the branch was cut, which is exactly the event the map has to absorb
rather than be surprised by. The six files did not move — a3 is a Python fix —
so the entry repeats a2's digests, and
`test_two_tags_agree_unless_the_divergence_is_exactly_grandfathered` is what
proves that repetition is a fact rather than a paste.

Editing such a file does not migrate anything. It changes what a future
installation builds while every existing installation keeps whatever the old
bytes built — and `alembic_version` records only that the revision ran, never
which version of it. So the divergence is permanent, silent, and invisible to
every other gate here: the composed gate reads the CURRENT tree, the
live-catalog gate reads a database built from the CURRENT tree, and both agree
with each other while disagreeing with the field.

That premise is enforceable, which is what makes this a guard rather than a
convention. "Released" is not a judgement call: it is a tag on `origin`, and
every digest below is reproducible with

    git show <tag>:<path> | shasum -a 256

## Two halves, because either alone is defeatable

The **checked-in map** is what a reviewer reads: an edit to a released
migration shows up as a digest change in the diff, at the moment somebody could
still object. On its own it is defeatable in one commit — edit the file, update
the digest beside it, and the comparison agrees with itself.

So the map is also **cross-checked against the tags**: every recorded digest
must equal the SHA-256 of the blob git holds at that tag. Doctoring the map now
requires moving a tag on `origin`, which is a different and far more visible
act. Neither half is redundant; the first catches the honest mistake, the
second catches the map being brought into line with it.

The tag half follows the fail-closed oracle discipline #202 established for
`test_declared_publication.py`: a shallow or tagless checkout is a FAILURE, not
a skip, because "the oracle was unavailable" is never evidence that nothing is
wrong. It is runnable because that change gave the `unit` job `fetch-depth: 0`
— before it, this half would have been permanently skipped, which is why the
checked-in map is the primary and not merely a convenience.

## What this does NOT claim

It does not stop a released migration from being WRONG. A defect in shipped DDL
is repaired by a new revision that alters the result — the same discipline as
`ig_0007`, which verifies a prerequisite `ig_0001` should arguably have verified
and does not touch `ig_0001` to do it. This guard only insists the repair be
additive.

Approvals is the measured exception to one-byte-history: `ap_0001` shipped
three byte sets across a1-a4 before enrolment. `GRANDFATHERED_DIVERGENCES`
records that closed historical set and the canonical bytes retained by the
tree; it is not permission for a fourth edit. The static census is paired with
`tests/test_approvals_released_migration_upgrades.py`, which reconstructs each
tagged meaning and proves its additive upgrade on PostgreSQL.

## Scope

Every distribution named in ``DISTRIBUTIONS``. Each entry's digests were read
from its published tags when that lineage enrolled. That is the enrolment rule,
and it is the reason this is still not generalised to "every allowlisted
module": a distribution enters when the tag oracle has actually verified its
history, because a guard populated by guesswork is worse than an absent one.

Enrolment is therefore a data edit — a row in `DISTRIBUTIONS`, its tags in
`RELEASED_TAGS`, and its still-editable files in `UNRELEASED`. The
`test_every_migration_is_either_released_or_declared_unreleased` ratchet then
holds that distribution's whole versions directory, in both directions.

An unenrolled distribution is UNMONITORED here, not exempt (ADR-0018). The
difference is visible in `test_the_unmonitored_distributions_are_named`, which
lists exactly which allowlisted modules this file says nothing about.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_WORKFLOW = REPO_ROOT / ".github/workflows/ci.yml"

#: The distributions this file monitors, and where each keeps its lineage.
#:
#: A distribution is here because somebody read its tags. Everything else is
#: unmonitored and named as such by
#: `test_the_unmonitored_distributions_are_named`.
DISTRIBUTIONS: dict[str, Path] = {
    "dotmac-approvals": (
        REPO_ROOT / "packages/dotmac-approvals/src/dotmac_approvals/migrations/versions"
    ),
    "dotmac-integration": (
        REPO_ROOT
        / "packages/dotmac-integration/src/dotmac_integration/migrations/versions"
    ),
    "dotmac-entitlement-allocation": (
        REPO_ROOT
        / "packages/dotmac-entitlement-allocation/src/dotmac_entitlement_allocation"
        / "migrations/versions"
    ),
    "dotmac-files": (
        REPO_ROOT / "packages/dotmac-files/src/dotmac_files/migrations/versions"
    ),
    "dotmac-forms": (
        REPO_ROOT / "packages/dotmac-forms/src/dotmac_forms" / "migrations/versions"
    ),
    "dotmac-workflow-runtime": (
        REPO_ROOT
        / "packages/dotmac-workflow-runtime/src/dotmac_workflow_runtime"
        / "migrations/versions"
    ),
    "dotmac-platform-health": (
        REPO_ROOT
        / "packages/dotmac-platform-health/src/dotmac_platform_health"
        / "migrations/versions"
    ),
    "dotmac-support-access": (
        REPO_ROOT
        / "packages/dotmac-support-access/src/dotmac_support_access"
        / "migrations/versions"
    ),
    "dotmac-remote-access": (
        REPO_ROOT
        / "packages/dotmac-remote-access/src/dotmac_remote_access"
        / "migrations/versions"
    ),
    "dotmac-compliance-reporting": (
        REPO_ROOT
        / "packages/dotmac-compliance-reporting/src/dotmac_compliance_reporting"
        / "migrations/versions"
    ),
    "dotmac-ai-operations": (
        REPO_ROOT
        / "packages/dotmac-ai-operations/src/dotmac_ai_operations"
        / "migrations/versions"
    ),
    "dotmac-payments": (
        REPO_ROOT / "packages/dotmac-payments/src/dotmac_payments/migrations/versions"
    ),
    "dotmac-imports": (
        REPO_ROOT / "packages/dotmac-imports/src/dotmac_imports/migrations/versions"
    ),
    "dotmac-inbox-operations": (
        REPO_ROOT
        / "packages/dotmac-inbox-operations"
        / "src/dotmac_inbox_operations/migrations/versions"
    ),
    "dotmac-operational-escalations": (
        REPO_ROOT
        / "packages/dotmac-operational-escalations"
        / "src/dotmac_operational_escalations/migrations/versions"
    ),
    "dotmac-service-changes": (
        REPO_ROOT
        / "packages/dotmac-service-changes"
        / "src/dotmac_service_changes/migrations/versions"
    ),
    "dotmac-subscriptions": (
        REPO_ROOT
        / "packages/dotmac-subscriptions"
        / "src/dotmac_subscriptions/migrations/versions"
    ),
    "dotmac-billing": (
        REPO_ROOT / "packages/dotmac-billing" / "src/dotmac_billing/migrations/versions"
    ),
    "dotmac-collections": (
        REPO_ROOT
        / "packages/dotmac-collections"
        / "src/dotmac_collections/migrations/versions"
    ),
    "dotmac-fulfillment": (
        REPO_ROOT
        / "packages/dotmac-fulfillment"
        / "src/dotmac_fulfillment/migrations/versions"
    ),
    "dotmac-tax": (
        REPO_ROOT / "packages/dotmac-tax/src/dotmac_tax/migrations/versions"
    ),
    "dotmac-service-catalog": (
        REPO_ROOT
        / "packages/dotmac-service-catalog"
        / "src/dotmac_service_catalog/migrations/versions"
    ),
    "dotmac-customers": (
        REPO_ROOT
        / "packages/dotmac-customers"
        / "src/dotmac_customers/migrations/versions"
    ),
    "dotmac-fx-policy": (
        REPO_ROOT
        / "packages/dotmac-fx-policy"
        / "src/dotmac_fx_policy/migrations/versions"
    ),
    "dotmac-qualification": (
        REPO_ROOT
        / "packages/dotmac-qualification"
        / "src/dotmac_qualification/migrations/versions"
    ),
    "dotmac-response-obligations": (
        REPO_ROOT
        / "packages/dotmac-response-obligations"
        / "src/dotmac_response_obligations/migrations/versions"
    ),
    "dotmac-sales": (
        REPO_ROOT / "packages/dotmac-sales" / "src/dotmac_sales/migrations/versions"
    ),
    "dotmac-service-access-policy": (
        REPO_ROOT
        / "packages/dotmac-service-access-policy"
        / "src/dotmac_service_access_policy/migrations/versions"
    ),
    "dotmac-services": (
        REPO_ROOT
        / "packages/dotmac-services"
        / "src/dotmac_services/migrations/versions"
    ),
    "dotmac-template-studio": (
        REPO_ROOT
        / "packages/dotmac-template-studio"
        / "src/dotmac_template_studio/migrations/versions"
    ),
    "dotmac-commercial-agreements": (
        REPO_ROOT
        / "packages/dotmac-commercial-agreements"
        / "src/dotmac_commercial_agreements/migrations/versions"
    ),
}

#: The glob that enumerates one distribution's lineage on disk. Derived from
#: the module's migration prefix, so the ratchet cannot be defeated by a file
#: the pattern happens not to match.
LINEAGE_GLOBS: dict[str, str] = {
    "dotmac-approvals": "ap_*.py",
    "dotmac-integration": "ig_*.py",
    "dotmac-entitlement-allocation": "ea_*.py",
    "dotmac-files": "fi_*.py",
    "dotmac-forms": "fm_*.py",
    "dotmac-workflow-runtime": "wr_*.py",
    "dotmac-platform-health": "ph_*.py",
    "dotmac-support-access": "sup_*.py",
    "dotmac-remote-access": "ra_*.py",
    "dotmac-compliance-reporting": "cr_*.py",
    "dotmac-ai-operations": "ao_*.py",
    "dotmac-payments": "pm_*.py",
    "dotmac-imports": "im_*.py",
    "dotmac-inbox-operations": "io_*.py",
    "dotmac-operational-escalations": "oe_*.py",
    "dotmac-service-changes": "sch_*.py",
    "dotmac-subscriptions": "su_*.py",
    "dotmac-billing": "bi_*.py",
    "dotmac-collections": "cl_*.py",
    "dotmac-fulfillment": "fu_*.py",
    "dotmac-tax": "tx_*.py",
    "dotmac-service-catalog": "sc_*.py",
    "dotmac-customers": "cu_*.py",
    "dotmac-fx-policy": "fx_*.py",
    "dotmac-qualification": "qu_*.py",
    "dotmac-response-obligations": "ro_*.py",
    "dotmac-sales": "sa_*.py",
    "dotmac-service-access-policy": "sap_*.py",
    "dotmac-services": "se_*.py",
    "dotmac-template-studio": "ts_*.py",
    "dotmac-commercial-agreements": "cg_*.py",
}

TAG_PREFIXES: dict[str, str] = {
    "dotmac-approvals": "dotmac-approvals-v",
    "dotmac-integration": "dotmac-integration-v",
    "dotmac-entitlement-allocation": "dotmac-entitlement-allocation-v",
    "dotmac-files": "dotmac-files-v",
    "dotmac-forms": "dotmac-forms-v",
    "dotmac-workflow-runtime": "dotmac-workflow-runtime-v",
    "dotmac-platform-health": "dotmac-platform-health-v",
    "dotmac-support-access": "dotmac-support-access-v",
    "dotmac-remote-access": "dotmac-remote-access-v",
    "dotmac-compliance-reporting": "dotmac-compliance-reporting-v",
    "dotmac-ai-operations": "dotmac-ai-operations-v",
    "dotmac-payments": "dotmac-payments-v",
    "dotmac-imports": "dotmac-imports-v",
    "dotmac-inbox-operations": "dotmac-inbox-operations-v",
    "dotmac-operational-escalations": "dotmac-operational-escalations-v",
    "dotmac-service-changes": "dotmac-service-changes-v",
    "dotmac-subscriptions": "dotmac-subscriptions-v",
    "dotmac-billing": "dotmac-billing-v",
    "dotmac-collections": "dotmac-collections-v",
    "dotmac-fulfillment": "dotmac-fulfillment-v",
    "dotmac-tax": "dotmac-tax-v",
    "dotmac-service-catalog": "dotmac-service-catalog-v",
    "dotmac-customers": "dotmac-customers-v",
    "dotmac-fx-policy": "dotmac-fx-policy-v",
    "dotmac-qualification": "dotmac-qualification-v",
    "dotmac-response-obligations": "dotmac-response-obligations-v",
    "dotmac-sales": "dotmac-sales-v",
    "dotmac-service-access-policy": "dotmac-service-access-policy-v",
    "dotmac-services": "dotmac-services-v",
    "dotmac-template-studio": "dotmac-template-studio-v",
    "dotmac-commercial-agreements": "dotmac-commercial-agreements-v",
}

#: Kept for the many call sites that only need integration's directory.
VERSIONS = DISTRIBUTIONS["dotmac-integration"]

#: `tag -> (distribution, exact peeled commit, {filename: sha256 at that tag})`.
#:
#: Every one is an annotated tag created by `release-module.yml` and present on
#: `origin`; the full peeled commit is recorded and cross-checked so the tag
#: object and unchanged migration bytes cannot hide a moved coordinate.
RELEASED_TAGS: dict[str, tuple[str, str, dict[str, str]]] = {
    # ── dotmac-approvals ────────────────────────────────────────────────────
    # ap_0001 was edited in place twice before this guard enrolled the module.
    # The exact three historical byte sets are recorded here; the explicit
    # grandfathered-divergence ledger below decides which one the tree retains.
    "dotmac-approvals-v0.1.0a1": (
        "dotmac-approvals",
        "221f6868651426397e6e8443ca8b544234648247",
        {
            "ap_0001_approvals.py": (
                "ec5e1aa9e504de8143eebaafacb0615cf24b6ea930648f5b9cfd1a9afc2db70e"
            ),
        },
    ),
    "dotmac-approvals-v0.1.0a2": (
        "dotmac-approvals",
        "3e1f8012c4f45369b6801a709a270d71c5c95a8d",
        {
            "ap_0001_approvals.py": (
                "6c7b3263e05f860982dda125439171f62bba716d36d95b21e2c3a3224f19ad6a"
            ),
        },
    ),
    "dotmac-approvals-v0.1.0a3": (
        "dotmac-approvals",
        "16f11a9ef0df7697558904efa969294bafc3fab3",
        {
            "ap_0001_approvals.py": (
                "102110e3e50c2ebfe0e73c5eb5e77bafe014e4835edad45a41a91a9ae0c144cb"
            ),
        },
    ),
    "dotmac-approvals-v0.1.0a4": (
        "dotmac-approvals",
        "f013c7e0e0be9f704d3afbc13e233fde8950a1be",
        {
            "ap_0001_approvals.py": (
                "102110e3e50c2ebfe0e73c5eb5e77bafe014e4835edad45a41a91a9ae0c144cb"
            ),
        },
    ),
    "dotmac-approvals-v0.1.0a5": (
        "dotmac-approvals",
        "8d4ddfd9e285da06ce1fdd29b59f1b483d6ea38c",
        {
            "ap_0001_approvals.py": (
                "102110e3e50c2ebfe0e73c5eb5e77bafe014e4835edad45a41a91a9ae0c144cb"
            ),
            "ap_0002_outbox_relay.py": (
                "6aace60a4925ad5f5c693b81a356807c1ad2b9ffe1664fdfcd1417429d127e2d"
            ),
        },
    ),
    "dotmac-integration-v0.1.0a17": (
        "dotmac-integration",
        "2cab76b442e6cc6c8ed81a409d943ba250351c3d",
        {
            "ig_0001_connector_control_plane.py": (
                "dd9d566c4708980fa4d5c5c9c13301b9d9b558ed622a15712dd98c2148d745f1"
            ),
            "ig_0002_execution.py": (
                "745f1b23ccaf45964099c41b6aa5ee7a63b2623a3cf9a1c3736000046ae33d42"
            ),
            "ig_0003_ingress_endpoint.py": (
                "feb1a66e2f0f1558bea00a221c02a9e1da5a4bc6536c35a93805d0681f670066"
            ),
            "ig_0004_destinations.py": (
                "80da09cbb492006a3cf6334466d4c79e3ee6cce676013edfb897845b09d38201"
            ),
            "ig_0005_receipt_delivery.py": (
                "b762d17591ccd877143c36a72269b083adab13ab3a57e326b20aa9dd3d99371d"
            ),
            "ig_0006_retention.py": (
                "51a40ae5290e71baa2879b9bb87ea7bb06f75d5372ebdfc378eed6e836a42aaa"
            ),
            "ig_0007_idempotency_ledger.py": (
                "9f6336e88e016c37d8c5a1b6d0548f8a5a91bde6e41a5093676709136c68e54b"
            ),
            "ig_0008_platform_audit_log.py": (
                "1e2cb215be0e71edf1af33b41cd53630ba9583168c2ff270c568483fdff15825"
            ),
            "ig_0009_product_port_descriptors.py": (
                "f95ec953d0ec9d561b5d7d438d1865e817fc4d15b2178c87e5f67350a07ab2d9"
            ),
            "ig_0010_shadow_evidence.py": (
                "eb897df97435c63ec4844753d8afa497391fa2eabfa0673312725995c231b4ed"
            ),
            "ig_0011_replay_retention.py": (
                "96336372ac879518ad46f6657b8c81cf60afd133d500c4bc6310017f94c59b42"
            ),
            "ig_0012_delivery_evidence.py": (
                "61ef4e096dae74b7646608257614f4497af125edda63a6c270e402a63c2cfdd3"
            ),
            "ig_0013_delivery_result.py": (
                "28dfc210785c3c22a8d883239fe37727cbf14dc80e7e26058df4d32f387f14ea"
            ),
            "ig_0014_polling_evidence.py": (
                "07dea79c6650aefd205bb67500abf08cb9f0464766d470bb218ec181302549be"
            ),
            "ig_0015_descriptor_contract.py": (
                "ea7f03fcd60eee9688846f67934b9d56a5451020e46514ad8a379bd8e8b85503"
            ),
        },
    ),
    "dotmac-integration-v0.1.0a16": (
        "dotmac-integration",
        "dcab4559b6dcc2c38737dd65ce6bb2f5ba59df0e",
        {
            "ig_0001_connector_control_plane.py": (
                "dd9d566c4708980fa4d5c5c9c13301b9d9b558ed622a15712dd98c2148d745f1"
            ),
            "ig_0002_execution.py": (
                "745f1b23ccaf45964099c41b6aa5ee7a63b2623a3cf9a1c3736000046ae33d42"
            ),
            "ig_0003_ingress_endpoint.py": (
                "feb1a66e2f0f1558bea00a221c02a9e1da5a4bc6536c35a93805d0681f670066"
            ),
            "ig_0004_destinations.py": (
                "80da09cbb492006a3cf6334466d4c79e3ee6cce676013edfb897845b09d38201"
            ),
            "ig_0005_receipt_delivery.py": (
                "b762d17591ccd877143c36a72269b083adab13ab3a57e326b20aa9dd3d99371d"
            ),
            "ig_0006_retention.py": (
                "51a40ae5290e71baa2879b9bb87ea7bb06f75d5372ebdfc378eed6e836a42aaa"
            ),
            "ig_0007_idempotency_ledger.py": (
                "9f6336e88e016c37d8c5a1b6d0548f8a5a91bde6e41a5093676709136c68e54b"
            ),
            "ig_0008_platform_audit_log.py": (
                "1e2cb215be0e71edf1af33b41cd53630ba9583168c2ff270c568483fdff15825"
            ),
            "ig_0009_product_port_descriptors.py": (
                "f95ec953d0ec9d561b5d7d438d1865e817fc4d15b2178c87e5f67350a07ab2d9"
            ),
            "ig_0010_shadow_evidence.py": (
                "eb897df97435c63ec4844753d8afa497391fa2eabfa0673312725995c231b4ed"
            ),
            "ig_0011_replay_retention.py": (
                "96336372ac879518ad46f6657b8c81cf60afd133d500c4bc6310017f94c59b42"
            ),
            "ig_0012_delivery_evidence.py": (
                "61ef4e096dae74b7646608257614f4497af125edda63a6c270e402a63c2cfdd3"
            ),
            "ig_0013_delivery_result.py": (
                "28dfc210785c3c22a8d883239fe37727cbf14dc80e7e26058df4d32f387f14ea"
            ),
            "ig_0014_polling_evidence.py": (
                "07dea79c6650aefd205bb67500abf08cb9f0464766d470bb218ec181302549be"
            ),
        },
    ),
    "dotmac-integration-v0.1.0a15": (
        "dotmac-integration",
        "bd8d2262c26f62041cc22a813916066b9af85c7f",
        {
            "ig_0001_connector_control_plane.py": (
                "dd9d566c4708980fa4d5c5c9c13301b9d9b558ed622a15712dd98c2148d745f1"
            ),
            "ig_0002_execution.py": (
                "745f1b23ccaf45964099c41b6aa5ee7a63b2623a3cf9a1c3736000046ae33d42"
            ),
            "ig_0003_ingress_endpoint.py": (
                "feb1a66e2f0f1558bea00a221c02a9e1da5a4bc6536c35a93805d0681f670066"
            ),
            "ig_0004_destinations.py": (
                "80da09cbb492006a3cf6334466d4c79e3ee6cce676013edfb897845b09d38201"
            ),
            "ig_0005_receipt_delivery.py": (
                "b762d17591ccd877143c36a72269b083adab13ab3a57e326b20aa9dd3d99371d"
            ),
            "ig_0006_retention.py": (
                "51a40ae5290e71baa2879b9bb87ea7bb06f75d5372ebdfc378eed6e836a42aaa"
            ),
            "ig_0007_idempotency_ledger.py": (
                "9f6336e88e016c37d8c5a1b6d0548f8a5a91bde6e41a5093676709136c68e54b"
            ),
            "ig_0008_platform_audit_log.py": (
                "1e2cb215be0e71edf1af33b41cd53630ba9583168c2ff270c568483fdff15825"
            ),
            "ig_0009_product_port_descriptors.py": (
                "f95ec953d0ec9d561b5d7d438d1865e817fc4d15b2178c87e5f67350a07ab2d9"
            ),
            "ig_0010_shadow_evidence.py": (
                "eb897df97435c63ec4844753d8afa497391fa2eabfa0673312725995c231b4ed"
            ),
            "ig_0011_replay_retention.py": (
                "96336372ac879518ad46f6657b8c81cf60afd133d500c4bc6310017f94c59b42"
            ),
            "ig_0012_delivery_evidence.py": (
                "61ef4e096dae74b7646608257614f4497af125edda63a6c270e402a63c2cfdd3"
            ),
        },
    ),
    "dotmac-integration-v0.1.0a14": (
        "dotmac-integration",
        "70459efd468dd2dcc9e31693b9910b04fec21447",
        {
            "ig_0001_connector_control_plane.py": (
                "dd9d566c4708980fa4d5c5c9c13301b9d9b558ed622a15712dd98c2148d745f1"
            ),
            "ig_0002_execution.py": (
                "745f1b23ccaf45964099c41b6aa5ee7a63b2623a3cf9a1c3736000046ae33d42"
            ),
            "ig_0003_ingress_endpoint.py": (
                "feb1a66e2f0f1558bea00a221c02a9e1da5a4bc6536c35a93805d0681f670066"
            ),
            "ig_0004_destinations.py": (
                "80da09cbb492006a3cf6334466d4c79e3ee6cce676013edfb897845b09d38201"
            ),
            "ig_0005_receipt_delivery.py": (
                "b762d17591ccd877143c36a72269b083adab13ab3a57e326b20aa9dd3d99371d"
            ),
            "ig_0006_retention.py": (
                "51a40ae5290e71baa2879b9bb87ea7bb06f75d5372ebdfc378eed6e836a42aaa"
            ),
            "ig_0007_idempotency_ledger.py": (
                "9f6336e88e016c37d8c5a1b6d0548f8a5a91bde6e41a5093676709136c68e54b"
            ),
            "ig_0008_platform_audit_log.py": (
                "1e2cb215be0e71edf1af33b41cd53630ba9583168c2ff270c568483fdff15825"
            ),
            "ig_0009_product_port_descriptors.py": (
                "f95ec953d0ec9d561b5d7d438d1865e817fc4d15b2178c87e5f67350a07ab2d9"
            ),
            "ig_0010_shadow_evidence.py": (
                "eb897df97435c63ec4844753d8afa497391fa2eabfa0673312725995c231b4ed"
            ),
            "ig_0011_replay_retention.py": (
                "96336372ac879518ad46f6657b8c81cf60afd133d500c4bc6310017f94c59b42"
            ),
            "ig_0012_delivery_evidence.py": (
                "61ef4e096dae74b7646608257614f4497af125edda63a6c270e402a63c2cfdd3"
            ),
        },
    ),
    "dotmac-integration-v0.1.0a13": (
        "dotmac-integration",
        "46926fa4f4243f7cd0c00230684eef8ab25cf723",
        {
            "ig_0001_connector_control_plane.py": (
                "dd9d566c4708980fa4d5c5c9c13301b9d9b558ed622a15712dd98c2148d745f1"
            ),
            "ig_0002_execution.py": (
                "745f1b23ccaf45964099c41b6aa5ee7a63b2623a3cf9a1c3736000046ae33d42"
            ),
            "ig_0003_ingress_endpoint.py": (
                "feb1a66e2f0f1558bea00a221c02a9e1da5a4bc6536c35a93805d0681f670066"
            ),
            "ig_0004_destinations.py": (
                "80da09cbb492006a3cf6334466d4c79e3ee6cce676013edfb897845b09d38201"
            ),
            "ig_0005_receipt_delivery.py": (
                "b762d17591ccd877143c36a72269b083adab13ab3a57e326b20aa9dd3d99371d"
            ),
            "ig_0006_retention.py": (
                "51a40ae5290e71baa2879b9bb87ea7bb06f75d5372ebdfc378eed6e836a42aaa"
            ),
            "ig_0007_idempotency_ledger.py": (
                "9f6336e88e016c37d8c5a1b6d0548f8a5a91bde6e41a5093676709136c68e54b"
            ),
            "ig_0008_platform_audit_log.py": (
                "1e2cb215be0e71edf1af33b41cd53630ba9583168c2ff270c568483fdff15825"
            ),
            "ig_0009_product_port_descriptors.py": (
                "f95ec953d0ec9d561b5d7d438d1865e817fc4d15b2178c87e5f67350a07ab2d9"
            ),
            "ig_0010_shadow_evidence.py": (
                "eb897df97435c63ec4844753d8afa497391fa2eabfa0673312725995c231b4ed"
            ),
            "ig_0011_replay_retention.py": (
                "96336372ac879518ad46f6657b8c81cf60afd133d500c4bc6310017f94c59b42"
            ),
        },
    ),
    "dotmac-integration-v0.1.0a1": (
        "dotmac-integration",
        "1b1d62bebc4651273b2587fb607c49485fed123a",
        {
            "ig_0001_connector_control_plane.py": (
                "dd9d566c4708980fa4d5c5c9c13301b9d9b558ed622a15712dd98c2148d745f1"
            ),
            "ig_0002_execution.py": (
                "745f1b23ccaf45964099c41b6aa5ee7a63b2623a3cf9a1c3736000046ae33d42"
            ),
        },
    ),
    "dotmac-integration-v0.1.0a2": (
        "dotmac-integration",
        "aaa3b5435732f0b1bebdf894778d6615c05e3c12",
        {
            "ig_0001_connector_control_plane.py": (
                "dd9d566c4708980fa4d5c5c9c13301b9d9b558ed622a15712dd98c2148d745f1"
            ),
            "ig_0002_execution.py": (
                "745f1b23ccaf45964099c41b6aa5ee7a63b2623a3cf9a1c3736000046ae33d42"
            ),
            "ig_0003_ingress_endpoint.py": (
                "feb1a66e2f0f1558bea00a221c02a9e1da5a4bc6536c35a93805d0681f670066"
            ),
            "ig_0004_destinations.py": (
                "80da09cbb492006a3cf6334466d4c79e3ee6cce676013edfb897845b09d38201"
            ),
            "ig_0005_receipt_delivery.py": (
                "b762d17591ccd877143c36a72269b083adab13ab3a57e326b20aa9dd3d99371d"
            ),
            "ig_0006_retention.py": (
                "51a40ae5290e71baa2879b9bb87ea7bb06f75d5372ebdfc378eed6e836a42aaa"
            ),
        },
    ),
    # a3 is a Python-only fix (`ModeContractError` sanitisation, #201) shipped
    # with the same six migrations. Recorded anyway: "the lineage did not
    # change" is a claim, and an entry per tag is what checks it.
    "dotmac-integration-v0.1.0a3": (
        "dotmac-integration",
        "b14f66e65642ec636936ea649ad0e86249b6de5a",
        {
            "ig_0001_connector_control_plane.py": (
                "dd9d566c4708980fa4d5c5c9c13301b9d9b558ed622a15712dd98c2148d745f1"
            ),
            "ig_0002_execution.py": (
                "745f1b23ccaf45964099c41b6aa5ee7a63b2623a3cf9a1c3736000046ae33d42"
            ),
            "ig_0003_ingress_endpoint.py": (
                "feb1a66e2f0f1558bea00a221c02a9e1da5a4bc6536c35a93805d0681f670066"
            ),
            "ig_0004_destinations.py": (
                "80da09cbb492006a3cf6334466d4c79e3ee6cce676013edfb897845b09d38201"
            ),
            "ig_0005_receipt_delivery.py": (
                "b762d17591ccd877143c36a72269b083adab13ab3a57e326b20aa9dd3d99371d"
            ),
            "ig_0006_retention.py": (
                "51a40ae5290e71baa2879b9bb87ea7bb06f75d5372ebdfc378eed6e836a42aaa"
            ),
        },
    ),
    # a4 adds the DDL-free prerequisite verification revision. Its bytes are
    # now published history and may not remain in the editable set below.
    "dotmac-integration-v0.1.0a4": (
        "dotmac-integration",
        "306a40e29da8fa63a6ee9346f29665731b558cc5",
        {
            "ig_0001_connector_control_plane.py": (
                "dd9d566c4708980fa4d5c5c9c13301b9d9b558ed622a15712dd98c2148d745f1"
            ),
            "ig_0002_execution.py": (
                "745f1b23ccaf45964099c41b6aa5ee7a63b2623a3cf9a1c3736000046ae33d42"
            ),
            "ig_0003_ingress_endpoint.py": (
                "feb1a66e2f0f1558bea00a221c02a9e1da5a4bc6536c35a93805d0681f670066"
            ),
            "ig_0004_destinations.py": (
                "80da09cbb492006a3cf6334466d4c79e3ee6cce676013edfb897845b09d38201"
            ),
            "ig_0005_receipt_delivery.py": (
                "b762d17591ccd877143c36a72269b083adab13ab3a57e326b20aa9dd3d99371d"
            ),
            "ig_0006_retention.py": (
                "51a40ae5290e71baa2879b9bb87ea7bb06f75d5372ebdfc378eed6e836a42aaa"
            ),
            "ig_0007_idempotency_ledger.py": (
                "9f6336e88e016c37d8c5a1b6d0548f8a5a91bde6e41a5093676709136c68e54b"
            ),
        },
    ),
    # a5 is a Python/SPI release. It repeats a4's seven migration digests, which
    # is still a release-history fact rather than permission to omit the tag.
    "dotmac-integration-v0.1.0a5": (
        "dotmac-integration",
        "7828697ef11fb1ae765a5397dfa7dc221ae6207a",
        {
            "ig_0001_connector_control_plane.py": (
                "dd9d566c4708980fa4d5c5c9c13301b9d9b558ed622a15712dd98c2148d745f1"
            ),
            "ig_0002_execution.py": (
                "745f1b23ccaf45964099c41b6aa5ee7a63b2623a3cf9a1c3736000046ae33d42"
            ),
            "ig_0003_ingress_endpoint.py": (
                "feb1a66e2f0f1558bea00a221c02a9e1da5a4bc6536c35a93805d0681f670066"
            ),
            "ig_0004_destinations.py": (
                "80da09cbb492006a3cf6334466d4c79e3ee6cce676013edfb897845b09d38201"
            ),
            "ig_0005_receipt_delivery.py": (
                "b762d17591ccd877143c36a72269b083adab13ab3a57e326b20aa9dd3d99371d"
            ),
            "ig_0006_retention.py": (
                "51a40ae5290e71baa2879b9bb87ea7bb06f75d5372ebdfc378eed6e836a42aaa"
            ),
            "ig_0007_idempotency_ledger.py": (
                "9f6336e88e016c37d8c5a1b6d0548f8a5a91bde6e41a5093676709136c68e54b"
            ),
        },
    ),
    # a6 adds the DDL-free platform-audit prerequisite verifier. It was
    # published from main before the product-port branch merged, so ig_0009 is
    # deliberately absent from this immutable release record.
    "dotmac-integration-v0.1.0a6": (
        "dotmac-integration",
        "7e0543004864845f0035c9ec325e3f5064c281cc",
        {
            "ig_0001_connector_control_plane.py": (
                "dd9d566c4708980fa4d5c5c9c13301b9d9b558ed622a15712dd98c2148d745f1"
            ),
            "ig_0002_execution.py": (
                "745f1b23ccaf45964099c41b6aa5ee7a63b2623a3cf9a1c3736000046ae33d42"
            ),
            "ig_0003_ingress_endpoint.py": (
                "feb1a66e2f0f1558bea00a221c02a9e1da5a4bc6536c35a93805d0681f670066"
            ),
            "ig_0004_destinations.py": (
                "80da09cbb492006a3cf6334466d4c79e3ee6cce676013edfb897845b09d38201"
            ),
            "ig_0005_receipt_delivery.py": (
                "b762d17591ccd877143c36a72269b083adab13ab3a57e326b20aa9dd3d99371d"
            ),
            "ig_0006_retention.py": (
                "51a40ae5290e71baa2879b9bb87ea7bb06f75d5372ebdfc378eed6e836a42aaa"
            ),
            "ig_0007_idempotency_ledger.py": (
                "9f6336e88e016c37d8c5a1b6d0548f8a5a91bde6e41a5093676709136c68e54b"
            ),
            "ig_0008_platform_audit_log.py": (
                "1e2cb215be0e71edf1af33b41cd53630ba9583168c2ff270c568483fdff15825"
            ),
        },
    ),
    # a7 adds immutable product-port descriptor provenance and receipt-owned
    # provider identity. It was published from main while the independent
    # shadow-evidence branch was being rebased, so only ig_0010 remains
    # editable after this release record is reconciled.
    "dotmac-integration-v0.1.0a7": (
        "dotmac-integration",
        "c669b24ee8be09677e92f85b11619f6626ad998d",
        {
            "ig_0001_connector_control_plane.py": (
                "dd9d566c4708980fa4d5c5c9c13301b9d9b558ed622a15712dd98c2148d745f1"
            ),
            "ig_0002_execution.py": (
                "745f1b23ccaf45964099c41b6aa5ee7a63b2623a3cf9a1c3736000046ae33d42"
            ),
            "ig_0003_ingress_endpoint.py": (
                "feb1a66e2f0f1558bea00a221c02a9e1da5a4bc6536c35a93805d0681f670066"
            ),
            "ig_0004_destinations.py": (
                "80da09cbb492006a3cf6334466d4c79e3ee6cce676013edfb897845b09d38201"
            ),
            "ig_0005_receipt_delivery.py": (
                "b762d17591ccd877143c36a72269b083adab13ab3a57e326b20aa9dd3d99371d"
            ),
            "ig_0006_retention.py": (
                "51a40ae5290e71baa2879b9bb87ea7bb06f75d5372ebdfc378eed6e836a42aaa"
            ),
            "ig_0007_idempotency_ledger.py": (
                "9f6336e88e016c37d8c5a1b6d0548f8a5a91bde6e41a5093676709136c68e54b"
            ),
            "ig_0008_platform_audit_log.py": (
                "1e2cb215be0e71edf1af33b41cd53630ba9583168c2ff270c568483fdff15825"
            ),
            "ig_0009_product_port_descriptors.py": (
                "f95ec953d0ec9d561b5d7d438d1865e817fc4d15b2178c87e5f67350a07ab2d9"
            ),
        },
    ),
    # a8 adds the module-owned, indexed shadow-comparison evidence table. The
    # release workflow installed the exact published wheel from the private
    # index, registered its manifest and only then created this tag.
    "dotmac-integration-v0.1.0a8": (
        "dotmac-integration",
        "4b1e86753b010a60be38bb5346f89e493936d2f4",
        {
            "ig_0001_connector_control_plane.py": (
                "dd9d566c4708980fa4d5c5c9c13301b9d9b558ed622a15712dd98c2148d745f1"
            ),
            "ig_0002_execution.py": (
                "745f1b23ccaf45964099c41b6aa5ee7a63b2623a3cf9a1c3736000046ae33d42"
            ),
            "ig_0003_ingress_endpoint.py": (
                "feb1a66e2f0f1558bea00a221c02a9e1da5a4bc6536c35a93805d0681f670066"
            ),
            "ig_0004_destinations.py": (
                "80da09cbb492006a3cf6334466d4c79e3ee6cce676013edfb897845b09d38201"
            ),
            "ig_0005_receipt_delivery.py": (
                "b762d17591ccd877143c36a72269b083adab13ab3a57e326b20aa9dd3d99371d"
            ),
            "ig_0006_retention.py": (
                "51a40ae5290e71baa2879b9bb87ea7bb06f75d5372ebdfc378eed6e836a42aaa"
            ),
            "ig_0007_idempotency_ledger.py": (
                "9f6336e88e016c37d8c5a1b6d0548f8a5a91bde6e41a5093676709136c68e54b"
            ),
            "ig_0008_platform_audit_log.py": (
                "1e2cb215be0e71edf1af33b41cd53630ba9583168c2ff270c568483fdff15825"
            ),
            "ig_0009_product_port_descriptors.py": (
                "f95ec953d0ec9d561b5d7d438d1865e817fc4d15b2178c87e5f67350a07ab2d9"
            ),
            "ig_0010_shadow_evidence.py": (
                "eb897df97435c63ec4844753d8afa497391fa2eabfa0673312725995c231b4ed"
            ),
        },
    ),
    # a9 makes replay evidence finite and makes released legal-hold history
    # survive an eligible receipt delete. The release workflow installed the
    # exact wheel from the private index before creating this tag.
    "dotmac-integration-v0.1.0a9": (
        "dotmac-integration",
        "92ae7a6f9c8307797704deb615a24e59420a73c4",
        {
            "ig_0001_connector_control_plane.py": (
                "dd9d566c4708980fa4d5c5c9c13301b9d9b558ed622a15712dd98c2148d745f1"
            ),
            "ig_0002_execution.py": (
                "745f1b23ccaf45964099c41b6aa5ee7a63b2623a3cf9a1c3736000046ae33d42"
            ),
            "ig_0003_ingress_endpoint.py": (
                "feb1a66e2f0f1558bea00a221c02a9e1da5a4bc6536c35a93805d0681f670066"
            ),
            "ig_0004_destinations.py": (
                "80da09cbb492006a3cf6334466d4c79e3ee6cce676013edfb897845b09d38201"
            ),
            "ig_0005_receipt_delivery.py": (
                "b762d17591ccd877143c36a72269b083adab13ab3a57e326b20aa9dd3d99371d"
            ),
            "ig_0006_retention.py": (
                "51a40ae5290e71baa2879b9bb87ea7bb06f75d5372ebdfc378eed6e836a42aaa"
            ),
            "ig_0007_idempotency_ledger.py": (
                "9f6336e88e016c37d8c5a1b6d0548f8a5a91bde6e41a5093676709136c68e54b"
            ),
            "ig_0008_platform_audit_log.py": (
                "1e2cb215be0e71edf1af33b41cd53630ba9583168c2ff270c568483fdff15825"
            ),
            "ig_0009_product_port_descriptors.py": (
                "f95ec953d0ec9d561b5d7d438d1865e817fc4d15b2178c87e5f67350a07ab2d9"
            ),
            "ig_0010_shadow_evidence.py": (
                "eb897df97435c63ec4844753d8afa497391fa2eabfa0673312725995c231b4ed"
            ),
            "ig_0011_replay_retention.py": (
                "96336372ac879518ad46f6657b8c81cf60afd133d500c4bc6310017f94c59b42"
            ),
        },
    ),
    # a10 adds SPI 1.3 runtime declarations and changes no migration bytes.
    # The release workflow installed the exact wheel from the private index,
    # registered its manifest and only then created this tag.
    # a11 records the finite replay-evidence retention already on main; the
    # migration bytes are unchanged from a10 apart from ig_0011. The release
    # workflow installed the exact wheel from the private index, registered its
    # manifest and only then created this tag.
    "dotmac-integration-v0.1.0a12": (
        "dotmac-integration",
        "9f59d02b47ec9aa6a594705e12b2032a7168ae46",
        {
            "ig_0001_connector_control_plane.py": (
                "dd9d566c4708980fa4d5c5c9c13301b9d9b558ed622a15712dd98c2148d745f1"
            ),
            "ig_0002_execution.py": (
                "745f1b23ccaf45964099c41b6aa5ee7a63b2623a3cf9a1c3736000046ae33d42"
            ),
            "ig_0003_ingress_endpoint.py": (
                "feb1a66e2f0f1558bea00a221c02a9e1da5a4bc6536c35a93805d0681f670066"
            ),
            "ig_0004_destinations.py": (
                "80da09cbb492006a3cf6334466d4c79e3ee6cce676013edfb897845b09d38201"
            ),
            "ig_0005_receipt_delivery.py": (
                "b762d17591ccd877143c36a72269b083adab13ab3a57e326b20aa9dd3d99371d"
            ),
            "ig_0006_retention.py": (
                "51a40ae5290e71baa2879b9bb87ea7bb06f75d5372ebdfc378eed6e836a42aaa"
            ),
            "ig_0007_idempotency_ledger.py": (
                "9f6336e88e016c37d8c5a1b6d0548f8a5a91bde6e41a5093676709136c68e54b"
            ),
            "ig_0008_platform_audit_log.py": (
                "1e2cb215be0e71edf1af33b41cd53630ba9583168c2ff270c568483fdff15825"
            ),
            "ig_0009_product_port_descriptors.py": (
                "f95ec953d0ec9d561b5d7d438d1865e817fc4d15b2178c87e5f67350a07ab2d9"
            ),
            "ig_0010_shadow_evidence.py": (
                "eb897df97435c63ec4844753d8afa497391fa2eabfa0673312725995c231b4ed"
            ),
            "ig_0011_replay_retention.py": (
                "96336372ac879518ad46f6657b8c81cf60afd133d500c4bc6310017f94c59b42"
            ),
        },
    ),
    "dotmac-integration-v0.1.0a11": (
        "dotmac-integration",
        "f25df1adf7076634abc735546158c374eedd6c31",
        {
            "ig_0001_connector_control_plane.py": (
                "dd9d566c4708980fa4d5c5c9c13301b9d9b558ed622a15712dd98c2148d745f1"
            ),
            "ig_0002_execution.py": (
                "745f1b23ccaf45964099c41b6aa5ee7a63b2623a3cf9a1c3736000046ae33d42"
            ),
            "ig_0003_ingress_endpoint.py": (
                "feb1a66e2f0f1558bea00a221c02a9e1da5a4bc6536c35a93805d0681f670066"
            ),
            "ig_0004_destinations.py": (
                "80da09cbb492006a3cf6334466d4c79e3ee6cce676013edfb897845b09d38201"
            ),
            "ig_0005_receipt_delivery.py": (
                "b762d17591ccd877143c36a72269b083adab13ab3a57e326b20aa9dd3d99371d"
            ),
            "ig_0006_retention.py": (
                "51a40ae5290e71baa2879b9bb87ea7bb06f75d5372ebdfc378eed6e836a42aaa"
            ),
            "ig_0007_idempotency_ledger.py": (
                "9f6336e88e016c37d8c5a1b6d0548f8a5a91bde6e41a5093676709136c68e54b"
            ),
            "ig_0008_platform_audit_log.py": (
                "1e2cb215be0e71edf1af33b41cd53630ba9583168c2ff270c568483fdff15825"
            ),
            "ig_0009_product_port_descriptors.py": (
                "f95ec953d0ec9d561b5d7d438d1865e817fc4d15b2178c87e5f67350a07ab2d9"
            ),
            "ig_0010_shadow_evidence.py": (
                "eb897df97435c63ec4844753d8afa497391fa2eabfa0673312725995c231b4ed"
            ),
            "ig_0011_replay_retention.py": (
                "96336372ac879518ad46f6657b8c81cf60afd133d500c4bc6310017f94c59b42"
            ),
        },
    ),
    "dotmac-integration-v0.1.0a10": (
        "dotmac-integration",
        "7a5986420881e3e49945efc1dc51c653070e73b3",
        {
            "ig_0001_connector_control_plane.py": (
                "dd9d566c4708980fa4d5c5c9c13301b9d9b558ed622a15712dd98c2148d745f1"
            ),
            "ig_0002_execution.py": (
                "745f1b23ccaf45964099c41b6aa5ee7a63b2623a3cf9a1c3736000046ae33d42"
            ),
            "ig_0003_ingress_endpoint.py": (
                "feb1a66e2f0f1558bea00a221c02a9e1da5a4bc6536c35a93805d0681f670066"
            ),
            "ig_0004_destinations.py": (
                "80da09cbb492006a3cf6334466d4c79e3ee6cce676013edfb897845b09d38201"
            ),
            "ig_0005_receipt_delivery.py": (
                "b762d17591ccd877143c36a72269b083adab13ab3a57e326b20aa9dd3d99371d"
            ),
            "ig_0006_retention.py": (
                "51a40ae5290e71baa2879b9bb87ea7bb06f75d5372ebdfc378eed6e836a42aaa"
            ),
            "ig_0007_idempotency_ledger.py": (
                "9f6336e88e016c37d8c5a1b6d0548f8a5a91bde6e41a5093676709136c68e54b"
            ),
            "ig_0008_platform_audit_log.py": (
                "1e2cb215be0e71edf1af33b41cd53630ba9583168c2ff270c568483fdff15825"
            ),
            "ig_0009_product_port_descriptors.py": (
                "f95ec953d0ec9d561b5d7d438d1865e817fc4d15b2178c87e5f67350a07ab2d9"
            ),
            "ig_0010_shadow_evidence.py": (
                "eb897df97435c63ec4844753d8afa497391fa2eabfa0673312725995c231b4ed"
            ),
            "ig_0011_replay_retention.py": (
                "96336372ac879518ad46f6657b8c81cf60afd133d500c4bc6310017f94c59b42"
            ),
        },
    ),
    # ── dotmac-entitlement-allocation ───────────────────────────────────────
    #
    # Four tags, one migration, one digest. `ea_0001` has not moved a byte
    # since `0.1.0a1`: a2 exposed `versions_dir()`, a3 made the ORM
    # relationships class-bound, and a4 moved the tables from the `tables=`
    # slot to `platform_tables=` — all three are Python-only, and the fourth
    # changed a manifest declaration rather than any DDL.
    #
    # That is exactly the history that makes an in-place edit tempting: four
    # releases with nothing to show for them in this directory reads as "the
    # migration is still ours". It is not — those bytes have run in databases
    # this repository does not own, which is why `ea_0002` exists as its own
    # head instead of a `require_prerequisites` line appended to `ea_0001`.
    "dotmac-entitlement-allocation-v0.1.0a1": (
        "dotmac-entitlement-allocation",
        "847ce0b5ea89dabdc34c26029df86173da80a4ab",
        {
            "ea_0001_allocations.py": (
                "a06682b221ac454a4e6df778c3184be59b63bde4bb527eacb27977c940425e22"
            ),
        },
    ),
    "dotmac-entitlement-allocation-v0.1.0a2": (
        "dotmac-entitlement-allocation",
        "5ded8808e376b2eeaf6150bf095077ba1ba3de5b",
        {
            "ea_0001_allocations.py": (
                "a06682b221ac454a4e6df778c3184be59b63bde4bb527eacb27977c940425e22"
            ),
        },
    ),
    "dotmac-entitlement-allocation-v0.1.0a3": (
        "dotmac-entitlement-allocation",
        "c371b0fdbb22bd3d51fd34d36922b9bc1e9dae68",
        {
            "ea_0001_allocations.py": (
                "a06682b221ac454a4e6df778c3184be59b63bde4bb527eacb27977c940425e22"
            ),
        },
    ),
    "dotmac-entitlement-allocation-v0.1.0a4": (
        "dotmac-entitlement-allocation",
        "67bdfb8b404a92fd455473806a256995bb507524",
        {
            "ea_0001_allocations.py": (
                "a06682b221ac454a4e6df778c3184be59b63bde4bb527eacb27977c940425e22"
            ),
        },
    ),
    # a5 was never published. a6 is the next release and freezes both DDL-free
    # verifier revisions alongside the unchanged allocation table revision.
    "dotmac-entitlement-allocation-v0.1.0a6": (
        "dotmac-entitlement-allocation",
        "7e0543004864845f0035c9ec325e3f5064c281cc",
        {
            "ea_0001_allocations.py": (
                "a06682b221ac454a4e6df778c3184be59b63bde4bb527eacb27977c940425e22"
            ),
            "ea_0002_idempotency_ledger.py": (
                "56076edb3f086b6e00b510df95d7af3b35153e8795f7b66754c89a2ad90032c2"
            ),
            "ea_0003_platform_audit_log.py": (
                "63027541404d7f9c824cade44c247098aa5f19bdb6d70cf321c1452974e0e072"
            ),
        },
    ),
    # ── dotmac-files ────────────────────────────────────────────────────────
    # a2's root is the published atomic catalogue. a3 adds fi_0002 rather than
    # changing this digest, so an existing a2 installation and a fresh a3
    # installation converge through an ordinary Alembic upgrade.
    "dotmac-files-v0.1.0a2": (
        "dotmac-files",
        "b3e47855f14433c626b4ac3e7723414f7834601a",
        {
            "fi_0001_stored_files.py": (
                "58976eab44ccfaaa77af255c52f92ef333e650e89ee3f6808211820b3c3b4fd0"
            ),
        },
    ),
    "dotmac-files-v0.1.0a3": (
        "dotmac-files",
        "c6ef6cd7b13105bd95c3faf354ffee9032077625",
        {
            "fi_0001_stored_files.py": (
                "58976eab44ccfaaa77af255c52f92ef333e650e89ee3f6808211820b3c3b4fd0"
            ),
            "fi_0002_selectable_planes.py": (
                "9cdaf0da282402777d6c2e694c60d29f8078a0d48c64211e9a6a67dc1ac05581"
            ),
        },
    ),
    # ── the ADR-0040 composable-unit cohort ─────────────────────────────────
    #
    # Seven modules published in one serial series on 2026-08-22, each from the
    # same protected-main revision, each composition-checked against every
    # sibling already published and kernel 0.1.0a88. Enrolled here at their
    # FIRST release rather than after an in-place edit forced the issue, which
    # is the only cheap moment to do it: one migration, one digest, no history
    # to reconstruct and no divergence to grandfather.
    "dotmac-forms-v0.1.0a1": (
        "dotmac-forms",
        "8f52abc4f903d001f9fbb5dfd9bfe87434e1f2ce",
        {
            "fm_0001_forms.py": (
                "0778d4f950acf049851c6876dcd5d34bb6ef23f37a4dcc35c6af38af3e6b8267"
            ),
        },
    ),
    "dotmac-workflow-runtime-v0.1.0a1": (
        "dotmac-workflow-runtime",
        "8f52abc4f903d001f9fbb5dfd9bfe87434e1f2ce",
        {
            "wr_0001_runtime.py": (
                "f959ef451f951e8c1bf314f8e1e4438731685f5aaf040c8a01bae4da3bb8311f"
            ),
        },
    ),
    "dotmac-platform-health-v0.1.0a1": (
        "dotmac-platform-health",
        "8f52abc4f903d001f9fbb5dfd9bfe87434e1f2ce",
        {
            "ph_0001_platform_health.py": (
                "ed2be6b9a295f0e5a25f45c8d32e4168a33a25ecc4a59a69b0633606c1d52b9e"
            ),
        },
    ),
    "dotmac-support-access-v0.1.0a1": (
        "dotmac-support-access",
        "8f52abc4f903d001f9fbb5dfd9bfe87434e1f2ce",
        {
            "sup_0001_support_access.py": (
                "22b75f92e04756cf05bffce76e303b06a7afbb3170b527f180477657351890a9"
            ),
        },
    ),
    "dotmac-remote-access-v0.1.0a1": (
        "dotmac-remote-access",
        "8f52abc4f903d001f9fbb5dfd9bfe87434e1f2ce",
        {
            "ra_0001_remote_access.py": (
                "8958e4cb85ac62a1c923a62b7a4b4b25691e32a085dc81639f5f642b283f377a"
            ),
        },
    ),
    "dotmac-compliance-reporting-v0.1.0a1": (
        "dotmac-compliance-reporting",
        "8f52abc4f903d001f9fbb5dfd9bfe87434e1f2ce",
        {
            "cr_0001_compliance_reporting.py": (
                "82e16095b9ae50f397010657755fb95b18444052aca9222490f0c2dd6a7fcf31"
            ),
        },
    ),
    "dotmac-ai-operations-v0.1.0a1": (
        "dotmac-ai-operations",
        "8f52abc4f903d001f9fbb5dfd9bfe87434e1f2ce",
        {
            "ao_0001_ai_operations.py": (
                "211d35dfd814d6c93a5e984053c60a1a2444c176b5aa8d08066261f9cfd96538"
            ),
        },
    ),
    # ── dotmac-payments ──
    "dotmac-payments-v0.1.0a1": (
        "dotmac-payments",
        "04b9771320b865b66d1322660fc6d3590605c973",
        {
            "pm_0001_payment_intents.py": (
                "d3f253970bfb6ef1e98289bff1dc2853bf4964a995d406a92529527d3bdb55db"
            ),
        },
    ),
    # ── dotmac-imports ──
    "dotmac-imports-v0.1.0a2": (
        "dotmac-imports",
        "5876ffd0bce17172fa2dc6ac6d09b48d877fadf8",
        {
            "im_0001_import_runs.py": (
                "c6d6caa3765bf133da66f1c6fe9decb179a3a2a7638ec404e3bcae7dc4f5109a"
            ),
        },
    ),
    # ── dotmac-inbox-operations ──
    "dotmac-inbox-operations-v0.1.0a3": (
        "dotmac-inbox-operations",
        "5b2798b80f6ac903fb132a0b1c205dd1dde3c528",
        {
            "io_0001_inbox_operations.py": (
                "180e422c920b43cadbd05a52ac2c72b423a952a9f70094f5372ff29f1b932aa0"
            ),
            "io_0002_queue_admission.py": (
                "0ff834e0cf7082c211e685d5e5ae292283a90e2d83da796e4dc2944a32948a32"
            ),
            "io_0003_operational_safety.py": (
                "26607110cf3001f0134ec9942a85ef0eb96866968b0502f978c8529352394ac3"
            ),
        },
    ),
    # ── dotmac-operational-escalations ──
    "dotmac-operational-escalations-v0.1.0a1": (
        "dotmac-operational-escalations",
        "c006da75d4c04eb6e5b2f9fec715edebcbcdea30",
        {
            "oe_0001_escalation_policy.py": (
                "05928af9f19dd89c98d082c79076510d6519c6d418f3e907f080f56e9a394d3a"
            ),
        },
    ),
    # ── dotmac-service-changes ──
    "dotmac-service-changes-v0.1.0a1": (
        "dotmac-service-changes",
        "c7571b9cbe3ddc9f5687f208a39930001ace8401",
        {
            "sch_0001_service_changes.py": (
                "a01c0e1056246ab39a7420e1364b644c7c2d56a4959fe5ac76298fd612cda20a"
            ),
        },
    ),
    # ── dotmac-subscriptions ──
    "dotmac-subscriptions-v0.1.0a3": (
        "dotmac-subscriptions",
        "ad6c5824086f6f550447caeabe820e860cdfe23c",
        {
            "su_0001_subscriptions.py": (
                "bbc6a1da801259a734988c976800c404ce30f4a3b8cf3f24a48410f557e3f252"
            ),
            "su_0002_offer_pricing.py": (
                "3b1a8524cfd585bac895f63bf7a8f3dc1d9521cfd997b80f379488e31fd21210"
            ),
            "su_0003_billing_treatments.py": (
                "ccdb960c3ed40f852d913a640d12903b86acad481e95c3a87f59ce7e69e129dc"
            ),
        },
    ),
    "dotmac-subscriptions-v0.1.0a2": (
        "dotmac-subscriptions",
        "f91253d5e193918507e9f2e0768a76aefe5bbce0",
        {
            "su_0001_subscriptions.py": (
                "bbc6a1da801259a734988c976800c404ce30f4a3b8cf3f24a48410f557e3f252"
            ),
            "su_0002_offer_pricing.py": (
                "3b1a8524cfd585bac895f63bf7a8f3dc1d9521cfd997b80f379488e31fd21210"
            ),
        },
    ),
    "dotmac-subscriptions-v0.1.0a1": (
        "dotmac-subscriptions",
        "ffe483fb53f12dd7aee400a39e0c85ecf308470f",
        {
            "su_0001_subscriptions.py": (
                "bbc6a1da801259a734988c976800c404ce30f4a3b8cf3f24a48410f557e3f252"
            ),
        },
    ),
    # ── dotmac-billing ──
    "dotmac-billing-v0.1.0a1": (
        "dotmac-billing",
        "92a1626b16d7e068f92536d8cfcb2ef9b6f270c2",
        {
            "bi_0001_billing.py": (
                "f82a962e7c0745cf11e2c8187042a5af412dd80a254be5b2e0975d0c7aa36373"
            ),
        },
    ),
    # ── dotmac-collections ──
    "dotmac-collections-v0.1.0a1": (
        "dotmac-collections",
        "6ecf518a6985b8bf4b163eccb3de2fef171ecccc",
        {
            "cl_0001_collections.py": (
                "f718c94d4044bb925da92aa9e186ee1c0765d4e5d67b1bbc6bc66ccf161d86f4"
            ),
        },
    ),
    # ── dotmac-fulfillment ──
    "dotmac-fulfillment-v0.1.0a1": (
        "dotmac-fulfillment",
        "be02e28d11a0ba849b4974273f5a2d4bd7806a4a",
        {
            "fu_0001_fulfillment.py": (
                "fa41945d34a08fc7f96d529f7ccc1f103a2b90b5c7095153dcc08f4283afe254"
            ),
        },
    ),
    # ── dotmac-tax ──
    "dotmac-tax-v0.1.0a3": (
        "dotmac-tax",
        "531f7f8c37ce2fdf41ecbf2f9a7a9940264a18f9",
        {
            "tx_0001_tax.py": (
                "bf3091556eb5eac401e64cfe342a2d59c17b7d511c0c772aef034340b07012ab"
            ),
            "tx_0002_multi_tax.py": (
                "9b78094519fe8d0785735f3a4e3a37dacdb9901b88eda25c90cdb167474abde0"
            ),
            "tx_0003_result_fingerprint.py": (
                "dd9751218ce7e27922a6ae4a869f6b25061dfa4bb93e0db7e152ea834579d4b3"
            ),
        },
    ),
    "dotmac-tax-v0.1.0a2": (
        "dotmac-tax",
        "bd8d2262c26f62041cc22a813916066b9af85c7f",
        {
            "tx_0001_tax.py": (
                "bf3091556eb5eac401e64cfe342a2d59c17b7d511c0c772aef034340b07012ab"
            ),
            "tx_0002_multi_tax.py": (
                "9b78094519fe8d0785735f3a4e3a37dacdb9901b88eda25c90cdb167474abde0"
            ),
        },
    ),
    "dotmac-tax-v0.1.0a1": (
        "dotmac-tax",
        "20d24703e70e4d361de2f406165df4b36cbee507",
        {
            "tx_0001_tax.py": (
                "bf3091556eb5eac401e64cfe342a2d59c17b7d511c0c772aef034340b07012ab"
            ),
        },
    ),
    # ── dotmac-service-catalog ──
    "dotmac-service-catalog-v0.1.0a1": (
        "dotmac-service-catalog",
        "f91253d5e193918507e9f2e0768a76aefe5bbce0",
        {
            "sc_0001_technical_catalog.py": (
                "7fc940ad1ddb48adb2fec31201756b48b48fae90b466fc2aca1d0901eb0c6547"
            ),
        },
    ),
    # ── dotmac-customers ──
    "dotmac-customers-v0.1.0a1": (
        "dotmac-customers",
        "2e2e0eff49aafc54a84885007402cd6012073330",
        {
            "cu_0001_customer_accounts.py": (
                "0a6ba209d80c4dabf40e40dd4023b1d08803c92ed904fde95869ad9e480256c9"
            ),
        },
    ),
    # ── dotmac-fx-policy ──
    "dotmac-fx-policy-v0.1.0a1": (
        "dotmac-fx-policy",
        "a0901915c6dcc5cd0580cb86d942dd3e91507d76",
        {
            "fx_0001_fx_policy.py": (
                "e8e89ed213ff468efba088b20b4dce0e81940d7abbfeb2bff580330948d9b134"
            ),
        },
    ),
    # ── dotmac-qualification ──
    "dotmac-qualification-v0.1.0a1": (
        "dotmac-qualification",
        "3922235907ae83ab0d241d07793a7db2830c7366",
        {
            "qu_0001_qualification_evidence.py": (
                "85afb29ec41ab7dde076ddd3e6d64e1b959563ee8ea54ac776a5ff295e5ef930"
            ),
        },
    ),
    # ── dotmac-response-obligations ──
    "dotmac-response-obligations-v0.1.0a1": (
        "dotmac-response-obligations",
        "8afbd7db7a3c9cdf2b47a54355fd67da4c38f45d",
        {
            "ro_0001_response_obligations.py": (
                "24f9755152d8a688a96f4416173e64bc1ad337ee90cdf21c56f550a0260620a5"
            ),
        },
    ),
    # ── dotmac-sales ──
    "dotmac-sales-v0.1.0a1": (
        "dotmac-sales",
        "1eef66eddcde3cb051886e5a2a61bdb30dc972a5",
        {
            "sa_0001_sales.py": (
                "25d918ceda444edf244ca96096d02283682f8f46c014171138ae47900cfa2553"
            ),
        },
    ),
    # ── dotmac-service-access-policy ──
    "dotmac-service-access-policy-v0.1.0a1": (
        "dotmac-service-access-policy",
        "1029354b0619fa356cc43651c7f26a4ebaf0ac60",
        {
            "sap_0001_access_policy.py": (
                "7dc2cb8f39891bc2b963a77771cf33f8ce3f5c5d5010007a445af4ed7dccec54"
            ),
        },
    ),
    # ── dotmac-services ──
    "dotmac-services-v0.1.0a1": (
        "dotmac-services",
        "63169d01bff8a93503100464ca16aaa450fe0557",
        {
            "se_0001_service_lifecycle.py": (
                "6daef5fde446a943b5cbab8d2dd2948c75d432a177217ffef0ffe495dc0a00bb"
            ),
        },
    ),
    # ── dotmac-template-studio ──
    "dotmac-template-studio-v0.2.0a4": (
        "dotmac-template-studio",
        "ac5e439e622ec5adba94cf52f4f961f2c39a2d30",
        {
            "ts_0001_templates.py": (
                "511073d3225c8fa9be09c687500e0515efbb5d8fdc4859791e1e4f16ed7308f4"
            ),
            "ts_0002_notification_identity.py": (
                "b50c2387f54a877cf0f0ff772bcfc84083a973595c490d6facf6bf7a8f196088"
            ),
        },
    ),
    "dotmac-template-studio-v0.2.0a3": (
        "dotmac-template-studio",
        "5777ceb5b41414c389036342ed58262bbfb97f31",
        {
            "ts_0001_templates.py": (
                "511073d3225c8fa9be09c687500e0515efbb5d8fdc4859791e1e4f16ed7308f4"
            ),
            "ts_0002_notification_identity.py": (
                "b50c2387f54a877cf0f0ff772bcfc84083a973595c490d6facf6bf7a8f196088"
            ),
        },
    ),
    # ── dotmac-commercial-agreements ──
    "dotmac-commercial-agreements-v0.1.0a1": (
        "dotmac-commercial-agreements",
        "fead57bc93d6551450f5e6ae1c9de1296e27b0ae",
        {
            "cg_0001_agreements.py": (
                "ac9e5f698f1814381a5987274131b186e9b0c0237b03314164cd69aa3806ec38"
            ),
        },
    ),
    "dotmac-commercial-agreements-v0.1.0a2": (
        "dotmac-commercial-agreements",
        "42acc8b30f1bcaed1580d312fd33d7b5ef358817",
        {
            "cg_0001_agreements.py": (
                "ac9e5f698f1814381a5987274131b186e9b0c0237b03314164cd69aa3806ec38"
            ),
        },
    ),
}


@dataclass(frozen=True)
class GrandfatheredDivergence:
    """A released filename whose historical tags do not contain one byte set."""

    canonical_digest: str
    variants: tuple[tuple[str, frozenset[str]], ...]
    reason: str


#: The ONLY accepted released-byte divergence. This records damage already in
#: the registry; it does not authorize another in-place edit. The canonical
#: digest is the latest shipped shape and is the only byte set the current tree
#: may retain. Each historical variant is still exercised by the PostgreSQL
#: upgrade matrix in `tests/test_approvals_released_migration_upgrades.py`.
GRANDFATHERED_DIVERGENCES: dict[tuple[str, str], GrandfatheredDivergence] = {
    ("dotmac-approvals", "ap_0001_approvals.py"): GrandfatheredDivergence(
        canonical_digest=(
            "102110e3e50c2ebfe0e73c5eb5e77bafe014e4835edad45a41a91a9ae0c144cb"
        ),
        variants=(
            (
                "ec5e1aa9e504de8143eebaafacb0615cf24b6ea930648f5b9cfd1a9afc2db70e",
                frozenset({"dotmac-approvals-v0.1.0a1"}),
            ),
            (
                "6c7b3263e05f860982dda125439171f62bba716d36d95b21e2c3a3224f19ad6a",
                frozenset({"dotmac-approvals-v0.1.0a2"}),
            ),
            (
                "102110e3e50c2ebfe0e73c5eb5e77bafe014e4835edad45a41a91a9ae0c144cb",
                frozenset(
                    {
                        "dotmac-approvals-v0.1.0a3",
                        "dotmac-approvals-v0.1.0a4",
                        "dotmac-approvals-v0.1.0a5",
                    }
                ),
            ),
        ),
        reason=(
            "a1 built both planes; a2 inferred tenant installation from a "
            "binding; a3/a4/a5 require explicit plane selection. These releases "
            "already exist, so the divergence is preserved as evidence and "
            "each variant must upgrade to the canonical current lineage."
        ),
    ),
}


#: Migration files that exist in the tree and have NOT shipped in any tag, so
#: are still editable. This is the second direction of the ratchet: a new
#: migration must be named here, and a file may only move from here into
#: `RELEASED_TAGS` — never the other way, and never out of both.
#:
#: An empty set means every current migration shipped. A non-empty set names
#: the exact files that remain editable; Integration's ig_0015 is one current
#: example. The release lane does not wait for an open branch, which is why
#: "released" is read from tags rather than from an intended version number.
UNRELEASED: dict[str, frozenset[str]] = {
    "dotmac-approvals": frozenset(),
    "dotmac-integration": frozenset(),
    "dotmac-entitlement-allocation": frozenset(),
    "dotmac-files": frozenset(),
    "dotmac-forms": frozenset(),
    "dotmac-workflow-runtime": frozenset(),
    "dotmac-platform-health": frozenset(),
    "dotmac-support-access": frozenset(),
    "dotmac-remote-access": frozenset(),
    "dotmac-compliance-reporting": frozenset(),
    "dotmac-ai-operations": frozenset(),
    "dotmac-payments": frozenset(),
    "dotmac-imports": frozenset(),
    # a5's availability/transfer lineage: still editable until the
    # protected release tags it and its digest is recorded above.
    "dotmac-inbox-operations": frozenset({"io_0004_availability_transfers.py"}),
    "dotmac-operational-escalations": frozenset(),
    "dotmac-service-changes": frozenset(),
    "dotmac-subscriptions": frozenset(),
    "dotmac-billing": frozenset(),
    "dotmac-collections": frozenset(),
    "dotmac-fulfillment": frozenset(),
    "dotmac-tax": frozenset(),
    "dotmac-service-catalog": frozenset(),
    "dotmac-customers": frozenset(),
    "dotmac-fx-policy": frozenset(),
    "dotmac-qualification": frozenset(),
    "dotmac-response-obligations": frozenset(),
    "dotmac-sales": frozenset(),
    "dotmac-service-access-policy": frozenset(),
    "dotmac-services": frozenset(),
    "dotmac-template-studio": frozenset(),
    "dotmac-commercial-agreements": frozenset(),
}


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _drift(versions: Path, distribution: str = "dotmac-integration") -> list[str]:
    """Every released file of `distribution` in `versions` whose bytes moved.

    Takes the directory as an argument rather than reading the module constant,
    which is the whole reason the sensitivity proofs below can run: they point
    it at a deliberately damaged copy of the tree. `distribution` selects which
    tags apply, because one directory holds one lineage and the map now holds
    several.
    """
    problems: list[str] = []
    checked_divergences: set[tuple[str, str]] = set()
    for tag, (owner, commit, files) in sorted(RELEASED_TAGS.items()):
        if owner != distribution:
            continue
        for name, expected in sorted(files.items()):
            key = (owner, name)
            divergence = GRANDFATHERED_DIVERGENCES.get(key)
            if divergence is not None:
                if key in checked_divergences:
                    continue
                checked_divergences.add(key)
                path = versions / name
                if not path.is_file():
                    shipping_tags = sorted(
                        tag_name for _, tags in divergence.variants for tag_name in tags
                    )
                    problems.append(
                        f"{name} shipped with grandfathered variants in "
                        f"{shipping_tags} and is now MISSING — released history "
                        "cannot be withdrawn"
                    )
                    continue
                actual = _digest(path)
                if actual != divergence.canonical_digest:
                    problems.append(
                        f"{name} has grandfathered released variants but the "
                        "current tree must retain canonical sha256 "
                        f"{divergence.canonical_digest}; found {actual}"
                    )
                continue
            path = versions / name
            if not path.is_file():
                problems.append(
                    f"{name} shipped in {tag} ({commit}) and is now MISSING — a "
                    "released revision cannot be withdrawn; adopters have "
                    "already run it"
                )
                continue
            actual = _digest(path)
            if actual != expected:
                problems.append(
                    f"{name} shipped in {tag} ({commit}) as sha256 {expected} "
                    f"and is now {actual}"
                )
    return problems


def _shipping_tags(name: str) -> list[str]:
    """Every tag whose entry records `name`.

    Derived, never counted by hand: `ig_0001` was in two tags when this file
    was written and three by the time it merged, and a hardcoded expectation
    turns a released migration appearing in one more release into a red suite
    for no reason.
    """
    return sorted(tag for tag, (_, _, files) in RELEASED_TAGS.items() if name in files)


# ── The guard ───────────────────────────────────────────────────────────────


def test_the_map_records_something_to_check() -> None:
    """A digest map that emptied would make every test below pass having
    compared nothing — the exact failure ADR-0018 calls a guard with no
    sensitivity."""
    assert RELEASED_TAGS, "no released tags recorded; this file proves nothing"
    for tag, (owner, _, files) in RELEASED_TAGS.items():
        assert files, f"{tag} records no files"
        assert owner in DISTRIBUTIONS, (
            f"{tag} names distribution {owner!r}, which has no versions "
            "directory in DISTRIBUTIONS — the map would then check nothing"
        )
    # Every monitored distribution must actually be monitored. A row in
    # DISTRIBUTIONS with no tag would look enrolled and check nothing.
    covered = {owner for owner, _, _ in RELEASED_TAGS.values()}
    assert covered == set(DISTRIBUTIONS), sorted(set(DISTRIBUTIONS) - covered)
    missing_prefixes = sorted(set(DISTRIBUTIONS) - set(TAG_PREFIXES))
    assert set(TAG_PREFIXES) == set(DISTRIBUTIONS), (
        "every monitored distribution needs a tag prefix, or a later release "
        f"can evade the external tag oracle: {missing_prefixes}"
    )


@pytest.mark.parametrize("distribution", sorted(DISTRIBUTIONS))
def test_no_released_migration_has_been_edited(distribution: str) -> None:
    """The guard itself. Adopters ran these bytes; the tree must still hold
    them, or a repair was made in place instead of in a new revision."""
    problems = _drift(DISTRIBUTIONS[distribution], distribution)
    assert not problems, (
        f"{distribution}: released migrations were modified:\n" + "\n".join(problems)
    )


def test_two_tags_agree_unless_the_divergence_is_exactly_grandfathered() -> None:
    """No second byte history can hide behind the approvals exception."""
    seen: dict[tuple[str, str], tuple[str, str]] = {}
    divergent: dict[tuple[str, str], dict[str, set[str]]] = {}
    for tag, (owner, _, files) in sorted(RELEASED_TAGS.items()):
        for name, digest in sorted(files.items()):
            key = (owner, name)
            divergent.setdefault(key, {}).setdefault(digest, set()).add(tag)
            seen.setdefault(key, (tag, digest))
    assert seen, "no shared files to compare"

    actual_divergences = {
        key: variants for key, variants in divergent.items() if len(variants) > 1
    }
    assert set(actual_divergences) == set(GRANDFATHERED_DIVERGENCES), (
        "released-byte divergence is either undeclared or its exception is stale: "
        f"actual={sorted(actual_divergences)}, "
        f"declared={sorted(GRANDFATHERED_DIVERGENCES)}"
    )
    for key, actual in actual_divergences.items():
        declaration = GRANDFATHERED_DIVERGENCES[key]
        declared = {digest: set(tags) for digest, tags in declaration.variants}
        assert actual == declared, f"{key}: variant/tag census drifted"
        assert declaration.canonical_digest in actual
        assert declaration.reason.strip()
        assert _digest(DISTRIBUTIONS[key[0]] / key[1]) == (
            declaration.canonical_digest
        ), f"{key}: current tree no longer retains the canonical released bytes"


@pytest.mark.parametrize("distribution", sorted(DISTRIBUTIONS))
def test_every_migration_is_either_released_or_declared_unreleased(
    distribution: str,
) -> None:
    """The two-directional ratchet.

    Without this, the guard is trivially defeated in both directions: a new
    migration simply never enters the map, and a released one can be dropped
    from it in the same commit that edits it. Neither is possible if the union
    must equal the directory exactly.
    """
    versions = DISTRIBUTIONS[distribution]
    on_disk = {path.name for path in versions.glob(LINEAGE_GLOBS[distribution])}
    assert on_disk, f"{distribution}: no migration matched its lineage glob"
    released = {
        name
        for owner, _, files in RELEASED_TAGS.values()
        if owner == distribution
        for name in files
    }
    unreleased = UNRELEASED[distribution]

    assert not (released & unreleased), (
        f"{distribution}: {sorted(released & unreleased)} claimed as both "
        "released and unreleased — a file is one or the other"
    )
    missing = sorted(on_disk - released - unreleased)
    assert not missing, (
        f"{distribution}: migration(s) {missing} are in neither map. A new "
        "migration goes in UNRELEASED; record its digest under its tag when it "
        "ships"
    )
    stale = sorted((released | unreleased) - on_disk)
    assert not stale, (
        f"{distribution}: {stale} are recorded but not on disk — see the "
        "deletion message in `_drift` before removing anything"
    )


def test_the_unmonitored_distributions_are_named() -> None:
    """ADR-0018: unmonitored and exempt are different labels, and the
    difference has to be visible.

    A releasable module absent from `DISTRIBUTIONS` is not excused from the
    rule that released bytes are history — nothing here checks it, which is a
    weaker statement and a different one. Naming the set turns "this file
    covers two modules" from a fact a reader has to reconstruct into one the
    suite reports, and makes enrolling the next module a visible diff rather
    than a silent absence.

    Deliberately NOT an assertion that the set is empty. It will not be for a
    long time, and a failing gate nobody can fix is one somebody deletes.
    """
    allowlist = set(
        json.loads(
            (REPO_ROOT / ".github/release-modules.json").read_text(encoding="utf-8")
        )["modules"]
    )
    monitored = set(DISTRIBUTIONS)
    assert monitored <= allowlist, (
        f"{sorted(monitored - allowlist)} is monitored here but is not in the "
        "release allowlist — an unreleasable distribution has no released bytes"
    )
    unmonitored = sorted(allowlist - monitored)
    print(
        "released-migration guard — UNMONITORED (not exempt): "
        + (", ".join(unmonitored) or "none")
    )
    assert monitored, "no distribution is monitored; this file proves nothing"


# ── Sensitivity proofs ──────────────────────────────────────────────────────
#
# A guard nobody has watched fail is a guard nobody has tested. Both proofs
# damage a COPY of the tree, so they establish that `_drift` reports the change
# without any chance of leaving the real one modified.


def test_the_guard_catches_a_one_byte_edit_to_a_released_migration(
    tmp_path: Path,
) -> None:
    """The proof that matters: a whitespace-only change is still a change.

    The realistic way a released migration gets edited is not a rewrite — it is
    a formatter run, a typo fix in a docstring, or a comment added while reading
    it. If the guard only noticed semantic edits it would miss every one of
    those, and they are equally capable of making the checked-in lineage
    disagree with what shipped.
    """
    copy = tmp_path / "versions"
    shutil.copytree(VERSIONS, copy)
    assert not _drift(copy), "the copy must start clean or this proves nothing"

    victim = copy / "ig_0001_connector_control_plane.py"
    victim.write_bytes(victim.read_bytes() + b"\n")

    shipped_in = _shipping_tags("ig_0001_connector_control_plane.py")
    assert len(shipped_in) >= 2, "this proof wants a file that shipped more than once"
    problems = _drift(copy)
    assert len(problems) == len(shipped_in), problems
    for problem, tag in zip(problems, shipped_in, strict=True):
        assert tag in problem, "each release that shipped the file must be named"
        assert "ig_0001_connector_control_plane.py" in problem
        assert (
            "dd9d566c4708980fa4d5c5c9c13301b9d9b558ed622a15712dd98c2148d745f1"
            in problem
        ), "the message must name the digest that shipped, not just 'differs'"


def test_the_guard_catches_a_deleted_released_migration(tmp_path: Path) -> None:
    """Deletion is the other half, and `_digest` alone would raise
    `FileNotFoundError` here — an error, not a finding, and one a reader would
    take for a broken test rather than a withdrawn revision."""
    copy = tmp_path / "versions"
    shutil.copytree(VERSIONS, copy)
    (copy / "ig_0006_retention.py").unlink()

    problems = _drift(copy)
    assert len(problems) == len(_shipping_tags("ig_0006_retention.py")), problems
    for problem in problems:
        assert "ig_0006_retention.py" in problem
        assert "MISSING" in problem


def test_an_unreleased_migration_is_free_to_change(tmp_path: Path) -> None:
    """Specificity for the two above: `_drift` must fire on RELEASED bytes, not
    on any change at all. A guard that refused every edit to the directory would
    pass both proofs above and block all future work."""
    distribution = "dotmac-integration"
    copy = tmp_path / "versions"
    shutil.copytree(DISTRIBUTIONS[distribution], copy)
    victim = copy / "ig_9999_still_being_written.py"
    victim.write_text("revision = 'ig_9999_still_being_written'\n", encoding="utf-8")
    assert all(
        victim.name not in files for _, _, files in RELEASED_TAGS.values()
    ), "the sensitivity file must not be classified as released"
    assert not _drift(copy, distribution)


def test_the_guard_catches_an_edit_to_the_second_distributions_bytes(
    tmp_path: Path,
) -> None:
    """Enrolment is only real if the new rows are actually compared.

    `ea_0001` shipped in five tags with one digest, and every proof above walks
    integration's directory — so all of them would pass with the allocation
    rows present and never read. This damages the file `ea_0002` exists to
    avoid editing, and requires all five releases to be named.
    """
    distribution = "dotmac-entitlement-allocation"
    copy = tmp_path / "versions"
    shutil.copytree(DISTRIBUTIONS[distribution], copy)
    assert not _drift(copy, distribution), "the copy must start clean"

    victim = copy / "ea_0001_allocations.py"
    victim.write_bytes(victim.read_bytes() + b"\n# a formatter ran\n")

    shipped_in = _shipping_tags("ea_0001_allocations.py")
    assert len(shipped_in) == 5, shipped_in
    problems = _drift(copy, distribution)
    assert len(problems) == len(shipped_in), problems
    for problem, tag in zip(problems, shipped_in, strict=True):
        assert tag in problem
        assert "ea_0001_allocations.py" in problem
        assert (
            "a06682b221ac454a4e6df778c3184be59b63bde4bb527eacb27977c940425e22"
            in problem
        ), "the message must name the digest that shipped, not just 'differs'"


def test_the_grandfathered_divergence_refuses_a_fourth_byte_set(
    tmp_path: Path,
) -> None:
    """Sensitivity: grandfathering history is not permission to edit again."""
    distribution = "dotmac-approvals"
    copy = tmp_path / "versions"
    shutil.copytree(DISTRIBUTIONS[distribution], copy)
    assert not _drift(copy, distribution), "the canonical copy must start clean"

    victim = copy / "ap_0001_approvals.py"
    victim.write_bytes(victim.read_bytes() + b"\n# a fourth byte set\n")

    problems = _drift(copy, distribution)
    assert len(problems) == 1, problems
    assert "grandfathered released variants" in problems[0]
    assert "canonical sha256" in problems[0]
    assert (
        "102110e3e50c2ebfe0e73c5eb5e77bafe014e4835edad45a41a91a9ae0c144cb"
        in problems[0]
    )


def test_one_distributions_damage_is_not_attributed_to_the_other(
    tmp_path: Path,
) -> None:
    """The scoping proof the second distribution makes necessary.

    `_drift` filters by owner. Without that filter it would hunt every recorded
    filename in whichever directory it was handed — so an intact allocation
    lineage would be reported as four MISSING files every time integration's
    directory was checked, and a guard that fails loudly for the wrong module
    is one whose next real failure gets waved through.

    Damage integration; require the allocation lineage, on disk and untouched,
    to stay silent.
    """
    damaged = tmp_path / "integration"
    shutil.copytree(DISTRIBUTIONS["dotmac-integration"], damaged)
    victim = damaged / "ig_0002_execution.py"
    victim.write_bytes(victim.read_bytes() + b"\n")
    assert _drift(damaged, "dotmac-integration"), "the damage must be reported"

    intact = tmp_path / "allocation"
    shutil.copytree(DISTRIBUTIONS["dotmac-entitlement-allocation"], intact)
    assert not _drift(intact, "dotmac-entitlement-allocation")


# ── The map is cross-checked against the tags it claims to quote ────────────


def _tag_oracle() -> object:
    """The repository's ONE definition of a usable tag oracle.

    Reusing `declared_publication_sweep` rather than re-implementing `git tag`
    here: two answers to "can this checkout be trusted about tags?" is how one
    of them ends up lenient. Its refusals are `SweepRefused`, and they are
    propagated, never converted to a skip.
    """
    import importlib.util

    path = REPO_ROOT / "scripts/declared_publication_sweep.py"
    spec = importlib.util.spec_from_file_location("declared_publication_sweep", path)
    assert spec is not None and spec.loader is not None
    sweep = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sweep)
    return sweep


def _blob_digest(tag: str, name: str) -> str:
    """SHA-256 of the migration file as git holds it at `tag`.

    The directory comes from the tag's own recorded distribution, not from a
    module constant — with two lineages in the map, a fixed path would compare
    one distribution's tags against the other's files and fail on every one.
    """
    distribution, _, _ = RELEASED_TAGS[tag]
    relative = DISTRIBUTIONS[distribution].relative_to(REPO_ROOT) / name
    # Fixed argv, no shell. The only interpolated values are this file's own
    # literals — a tag and a path from `RELEASED_TAGS`. `git` from PATH is the
    # same trust assumption every other subprocess guard here makes.
    argv = ["git", "show", f"{tag}:{relative.as_posix()}"]
    result = subprocess.run(  # noqa: S603 # nosec B603 B607
        argv, cwd=REPO_ROOT, capture_output=True, check=False
    )
    assert result.returncode == 0, (
        f"`git show {tag}:{relative}` failed: {result.stderr.decode().strip()} "
        "— the map names a tag or a path this checkout does not have"
    )
    return hashlib.sha256(result.stdout).hexdigest()


def _tag_inventory_problems(tags: set[str]) -> list[str]:
    """Recorded and published tags must agree in both directions."""
    problems: list[str] = []
    recorded = set(RELEASED_TAGS)
    missing = sorted(recorded - tags)
    if missing:
        problems.append(f"recorded tags absent from checkout: {missing}")
    for distribution, prefix in sorted(TAG_PREFIXES.items()):
        published = {tag for tag in tags if tag.startswith(prefix)}
        recorded_for_distribution = {
            tag for tag, (owner, _, _) in RELEASED_TAGS.items() if owner == distribution
        }
        unrecorded = sorted(published - recorded_for_distribution)
        if unrecorded:
            problems.append(
                f"{distribution}: published tags missing from RELEASED_TAGS: "
                f"{unrecorded}"
            )
    return problems


def test_the_tag_oracle_is_usable_and_complete() -> None:
    """Fail closed, exactly as `test_declared_publication.py` now does.

    A shallow or tagless checkout cannot answer what a release contained. The
    previous version of that module treated an unusable oracle as a skip and
    ran green while checking nothing on every PR for weeks (#202). This asserts
    the oracle up front so the cross-check below cannot inherit that shape.
    """
    sweep = _tag_oracle()
    assert not sweep.is_shallow(REPO_ROOT), (  # type: ignore[attr-defined]
        "shallow checkout: the released-migration cross-check needs full "
        "history and tags — every CI job that reads release history must set "
        "fetch-depth: 0"
    )
    tags = set(sweep.git_tags(REPO_ROOT))  # type: ignore[attr-defined]
    problems = _tag_inventory_problems(tags)
    assert not problems, "released-tag inventory:\n" + "\n".join(problems)


def _job_fetches_full_history(workflow: dict[str, object], job_name: str) -> bool:
    jobs = workflow.get("jobs")
    if not isinstance(jobs, dict):
        return False
    job = jobs.get(job_name)
    if not isinstance(job, dict):
        return False
    steps = job.get("steps")
    if not isinstance(steps, list):
        return False
    for step in steps:
        if not isinstance(step, dict) or not str(step.get("uses", "")).startswith(
            "actions/checkout@"
        ):
            continue
        checkout_with = step.get("with")
        return isinstance(checkout_with, dict) and checkout_with.get("fetch-depth") == 0
    return False


def test_every_ci_job_that_reads_release_history_fetches_tags() -> None:
    """The unit oracle and PostgreSQL upgrade proofs both execute git show."""
    workflow = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    missing = [
        job_name
        for job_name in ("unit", "integration")
        if not _job_fetches_full_history(workflow, job_name)
    ]
    assert not missing, (
        f"CI jobs {missing} execute release-history proofs without a full checkout; "
        "set actions/checkout fetch-depth: 0 so published tags are evidence"
    )


def test_the_ci_tag_checkout_guard_detects_a_missing_fetch_depth() -> None:
    """Sensitivity: the detector must fail when a checkout loses its tag oracle."""
    workflow = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    checkout = next(
        step
        for step in workflow["jobs"]["integration"]["steps"]
        if str(step.get("uses", "")).startswith("actions/checkout@")
    )
    checkout.pop("with", None)
    assert not _job_fetches_full_history(workflow, "integration")


@pytest.mark.parametrize("distribution", sorted(TAG_PREFIXES))
def test_the_tag_inventory_rejects_an_unrecorded_future_release(
    distribution: str,
) -> None:
    """Sensitivity: every enrolled lineage refuses an unrecorded future tag."""
    fake = f"{TAG_PREFIXES[distribution]}99.0.0"
    problems = _tag_inventory_problems(set(RELEASED_TAGS) | {fake})
    assert problems == [
        f"{distribution}: published tags missing from RELEASED_TAGS: ['{fake}']"
    ]


@pytest.mark.parametrize("tag", sorted(RELEASED_TAGS))
def test_each_recorded_commit_is_the_exact_peeled_tag(tag: str) -> None:
    """A digest match cannot substitute for the tag's immutable coordinate."""
    recorded = RELEASED_TAGS[tag][1]
    argv = ["git", "rev-parse", f"{tag}^{{commit}}"]
    peeled = subprocess.run(  # noqa: S603 # nosec B603 B607
        argv,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert peeled.returncode == 0, peeled.stderr
    assert (
        len(recorded) == 40 and recorded == peeled.stdout.strip()
    ), f"{tag}: map records {recorded!r}, peeled tag is {peeled.stdout.strip()!r}"


@pytest.mark.parametrize("tag", sorted(RELEASED_TAGS))
def test_the_recorded_digests_are_what_the_tag_actually_holds(tag: str) -> None:
    """The half that makes the map hard to doctor.

    Editing a released migration and updating its digest here would satisfy
    every test above — the tree and the map would simply agree with each other
    about the wrong bytes. This compares the map to git, so the two can only be
    reconciled by moving a tag.

    It also proves the file SET: a released file quietly dropped from the map
    would go unnoticed by a digest comparison that only walks the map, so the
    tag's own file list is the expected set.
    """
    distribution, commit, files = RELEASED_TAGS[tag]
    relative = DISTRIBUTIONS[distribution].relative_to(REPO_ROOT).as_posix()
    argv = ["git", "ls-tree", "-r", "--name-only", tag, "--", relative]
    listing = subprocess.run(  # noqa: S603 # nosec B603 B607
        argv, cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )
    assert listing.returncode == 0, listing.stderr
    at_tag = {
        line.rsplit("/", 1)[-1]
        for line in listing.stdout.splitlines()
        if line.strip() and not line.endswith("/__init__.py")
    }
    assert at_tag == set(files), (
        f"{tag} ({commit}) contains {sorted(at_tag)} but the map records "
        f"{sorted(files)} — a released file is missing from, or invented in, "
        "this entry"
    )
    for name, expected in sorted(files.items()):
        assert _blob_digest(tag, name) == expected, (
            f"{tag}/{name}: the map records {expected} but git holds "
            f"{_blob_digest(tag, name)} at that tag — the map was edited to "
            "match a changed file instead of the change being reverted"
        )


def test_the_cross_check_would_catch_a_doctored_map() -> None:
    """Sensitivity for the two above, without touching a real tag.

    `_blob_digest` reads git; the assertion compares it to the map. Point it at
    a file whose recorded digest is deliberately wrong and it must disagree —
    otherwise the comparison is not reading git at all, which is exactly how a
    cross-check degrades into a second copy of the thing it checks.
    """
    # One derived example per enrolled distribution. A hardcoded spot-check
    # stopped at four owners while the registry grew past thirty, so its claim
    # of covering every path silently became false.
    examples: dict[str, tuple[str, str]] = {}
    for tag, (owner, _, files) in sorted(RELEASED_TAGS.items()):
        if files:
            examples.setdefault(owner, (tag, sorted(files)[0]))
    assert set(examples) == set(DISTRIBUTIONS)

    for tag, name in examples.values():
        actual = _blob_digest(tag, name)
        assert actual == RELEASED_TAGS[tag][2][name]
        assert actual != "0" * 64


@pytest.mark.parametrize("tag", sorted(RELEASED_TAGS))
def test_each_recorded_digest_is_a_sha256(tag: str) -> None:
    """A truncated or mistyped digest would compare unequal to everything and
    turn the guard into a permanent failure — or, pasted from the wrong column,
    into a permanent pass against a value nothing produces."""
    _, _, files = RELEASED_TAGS[tag]
    for name, digest in files.items():
        assert len(digest) == 64, f"{tag}/{name}: {digest!r} is not a sha256"
        assert set(digest) <= set("0123456789abcdef"), f"{tag}/{name}: {digest!r}"
