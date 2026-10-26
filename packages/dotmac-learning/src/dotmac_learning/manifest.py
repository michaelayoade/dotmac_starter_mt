"""Tenant-only declaration for learning storage and implemented audit consumers."""

from dotmac_kernel.modules import ModuleManifest
from dotmac_kernel.prerequisites import (
    MODULE_DATABASE_ROLES_V1,
    PARTY_PERSON_CATALOG_V1,
    TENANT_AUDIT_LOG_V1,
    TENANT_SCOPE_CATALOG_V1,
)

from dotmac_learning.attempt_models import ATTEMPT_TABLES
from dotmac_learning.curriculum_models import CURRICULUM_TABLES
from dotmac_learning.delivery_models import DELIVERY_TABLES
from dotmac_learning.models import TENANT_TABLES
from dotmac_learning.participation_models import PARTICIPATION_TABLES

module = ModuleManifest(
    code="learning",
    version="0.1.0a2+dev",
    core=False,
    short_code="learning",
    migration_prefix="lr",
    migration_branch="learning",
    tables=(
        *TENANT_TABLES,
        *CURRICULUM_TABLES,
        *PARTICIPATION_TABLES,
        *ATTEMPT_TABLES,
        *DELIVERY_TABLES,
    ),
    platform_tables=(),
    audit_actions=(
        "attempt_granted",
        "question_bank.draft_created",
        "question_bank.question_upserted",
        "question_bank.question_deleted",
        "question_bank.draft_discarded",
        "question_bank.published",
        "question_bank.version_retired",
        "question_bank.activity_repointed",
        "question_bank.imported",
        "question_bank.import_refused",
        "question_bank.exported",
        "announcement.created",
        "scheduling.update_session",
        "success_queue.acknowledge",
        "success_queue.assign",
        "success_queue.resolve",
        "success_queue.message_segment",
    ),
    requires=(
        TENANT_SCOPE_CATALOG_V1.name,
        MODULE_DATABASE_ROLES_V1.name,
        PARTY_PERSON_CATALOG_V1.name,
        TENANT_AUDIT_LOG_V1.name,
    ),
)

__all__ = ["module"]
