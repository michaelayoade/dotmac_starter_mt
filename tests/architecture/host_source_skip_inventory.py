"""The recorded, two-directional inventory of tests that cannot reach their
subject through `Executor`/`RecoveryExecutor` any more — and the reachability
analysis `test_deployment_foundation_host_source_skip_inventory.py` uses to
recompute it and compare.

## Why identity, not a count

`valid_host_source_kwargs()` (`tests/unit/host_source_stance.py`) now calls
`pytest.skip(...)` instead of returning admitting kwargs, because there is no
longer a host-source parameter on either executor to admit through. Michael's
ruling on the review of `061cf4bd`: a COUNT of skipped tests is not a ratchet
— one skipped test can be deleted and a different, unrelated one added in the
same file without moving the number, and a count-only guard would stay green
through that swap. The inventory here is keyed by `(path, test function
name)` instead, so a test's IDENTITY, not merely the total, is what is
recorded and checked in both directions:

* a test reaching `valid_host_source_kwargs()` that is NOT in this set is a
  new, unrecorded loss of coverage;
* an entry in this set whose test no longer reaches
  `valid_host_source_kwargs()` (renamed, rewritten to reach it a different
  way, or simply deleted) has VANISHED, and that is worth recording deliberately
  rather than letting the set silently shrink.

## Measured, not the 20 first assumed

`valid_host_source_kwargs()` is called from 20 SOURCE LOCATIONS across six
files (a literal `grep -c` of `valid_host_source_kwargs()` call expressions).
But most of those 20 locations are inside a SHARED per-file helper — an
`_executor(...)` builder, a bare `run(...)` driver — that many individual
`test_*` functions call. The number of TEST FUNCTIONS that will actually
report as skipped, computed by tracing the real call graph from every
`test_*` function to `valid_host_source_kwargs()` (see `tests_reaching`
below), is **73**, not 20. Both counts are real facts about different
things — "20" answers "how many places call the fixture", "73" answers "how
many tests lose their subject" — and the inventory below is built at the
73-test granularity, because that is the one the ratchet's own stated
purpose (test identity, not a count) requires.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

#: The name whose reachability from a `test_*` function marks that test as
#: lost — `valid_host_source_kwargs()` calling `pytest.skip(...)` is the
#: ONLY thing that removes a test's ability to reach its subject through
#: either executor; nothing else in this analysis is a proxy for it.
TARGET_CALL = "valid_host_source_kwargs"

SKIP_REASON = (
    "unreachable: neither Executor nor RecoveryExecutor accepts a "
    "host-source parameter of any kind any more, and "
    "require_host_source(receipt=None) always refuses — see "
    "tests/unit/host_source_stance.py::valid_host_source_kwargs"
)

#: What retires this entry: a genuine trusted-provenance admission path
#: landing for either executor (see the CHANGELOG's "Trusted provenance is
#: separate, future work" note). Not a count, not a date, not "someone
#: rewrote the test a different way" — the CAPABILITY this whole PR
#: withheld.
RETIRE_WHEN = "trusted-provenance-admission"


def _called_names(node: ast.AST) -> set[str]:
    """Every name directly called (`f(...)`) inside `node`'s subtree.

    Deliberately excludes `ast.Attribute` calls (`obj.method(...)`): matching
    on the bare attribute name collides with unrelated methods that happen to
    share a name with a module-level helper (`executor.run(...)` is not a
    call to this module's `def run(...):`), which produced false positives
    the first time this analysis was run.
    """
    names: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
            names.add(n.func.id)
    return names


def tests_reaching(path: Path, target: str = TARGET_CALL) -> set[str]:
    """Every `test_*` function in `path` that transitively calls `target`,
    through any chain of same-file, top-level helper functions.

    A static, same-file call-graph reachability analysis — it does not
    resolve calls across module boundaries (a test importing a helper from
    ANOTHER test file would not be traced further than the import), which is
    sufficient here because every file in `SKIP_INVENTORY` defines its own
    `_executor`/`run`-shaped helper locally.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    funcs: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    direct_calls = {
        name: _called_names(node) & set(funcs) for name, node in funcs.items()
    }
    direct_hit = {name: target in _called_names(node) for name, node in funcs.items()}

    def reaches(name: str, seen: set[str]) -> bool:
        if name in seen:
            return False
        seen.add(name)
        if direct_hit.get(name):
            return True
        return any(reaches(callee, seen) for callee in direct_calls.get(name, ()))

    return {name for name in funcs if name.startswith("test_") and reaches(name, set())}


#: THE RECORDED INVENTORY. 73 `(relative path, test function)` pairs,
#: measured by running `tests_reaching` over the six files below on
#: `061cf4bd`. Every entry shares `SKIP_REASON` and `RETIRE_WHEN` — recorded
#: once, above, rather than per entry, because there is exactly one reason
#: and one retirement condition for all of them.
SKIP_INVENTORY: frozenset[tuple[str, str]] = frozenset(
    {
        (
            "tests/unit/test_deployment_foundation_lock_capability.py",
            "test_a_rollback_outside_any_deployment_lock_is_refused",
        ),
        (
            "tests/unit/test_deployment_foundation_lock_capability.py",
            "test_a_run_outside_any_deployment_lock_is_refused",
        ),
        (
            "tests/unit/test_deployment_foundation_lock_capability.py",
            "test_a_run_under_a_genuinely_held_token_is_not_refused",
        ),
        (
            "tests/unit/test_deployment_foundation_lock_capability.py",
            "test_a_run_with_an_expired_token_mutates_nothing",
        ),
        (
            "tests/unit/test_deployment_foundation_external_recovery.py",
            "test_a_missing_recovery_receipt_refuses_before_any_effect",
        ),
        (
            "tests/unit/test_deployment_foundation_external_recovery.py",
            "test_an_accepted_recovery_receipt_lets_the_deploy_proceed",
        ),
        (
            "tests/unit/test_deployment_foundation_external_recovery.py",
            "test_an_unsigned_recovery_receipt_refuses_at_the_engine",
        ),
        (
            "tests/unit/test_deployment_foundation_execution_binding.py",
            "test_a_host_that_moved_after_authorization_refuses_with_zero_effects",
        ),
        (
            "tests/unit/test_deployment_foundation_execution_binding.py",
            "test_a_plan_for_an_unauthorized_image_produces_zero_effects",
        ),
        (
            "tests/unit/test_deployment_foundation_execution_binding.py",
            "test_a_role_that_never_becomes_ready_fails_the_deployment",
        ),
        (
            "tests/unit/test_deployment_foundation_execution_binding.py",
            "test_a_second_execution_of_one_authorization_switches_again_not_silently",
        ),
        (
            "tests/unit/test_deployment_foundation_execution_binding.py",
            "test_absent_execution_plan_produces_zero_effects",
        ),
        (
            "tests/unit/test_deployment_foundation_execution_binding.py",
            "test_an_empty_prestate_is_a_claim_a_populated_host_fails",
        ),
        (
            "tests/unit/test_deployment_foundation_execution_binding.py",
            "test_an_unfrozen_plan_produces_zero_effects",
        ),
        (
            "tests/unit/test_deployment_foundation_execution_binding.py",
            "test_evidence_that_does_not_read_back_fails_the_deployment",
        ),
        (
            "tests/unit/test_deployment_foundation_execution_binding.py",
            "test_migration_family_work_runs_in_the_candidate_image",
        ),
        (
            "tests/unit/test_deployment_foundation_execution_binding.py",
            "test_the_exact_authorized_tuple_mutates_once",
        ),
        (
            "tests/unit/test_deployment_foundation_execution_binding.py",
            "test_the_replay_coordinate_reaches_the_execution_report",
        ),
        (
            "tests/unit/test_deployment_foundation_failure_injection.py",
            "test_a_backup_returning_no_checksum_is_refused_at_the_backup_step",
        ),
        (
            "tests/unit/test_deployment_foundation_failure_injection.py",
            "test_a_candidate_started_on_the_wrong_digest_is_refused",
        ),
        (
            "tests/unit/test_deployment_foundation_failure_injection.py",
            "test_a_candidate_that_never_becomes_ready_is_not_handed_traffic",
        ),
        (
            "tests/unit/test_deployment_foundation_failure_injection.py",
            "test_a_correct_deployment_succeeds_end_to_end",
        ),
        (
            "tests/unit/test_deployment_foundation_failure_injection.py",
            "test_a_corrupt_backup_fails_verification_and_stops_the_deployment",
        ),
        (
            "tests/unit/test_deployment_foundation_failure_injection.py",
            "test_a_dirty_checkout_is_refused",
        ),
        (
            "tests/unit/test_deployment_foundation_failure_injection.py",
            "test_a_failed_backup_stops_the_deployment_before_any_ddl",
        ),
        (
            "tests/unit/test_deployment_foundation_failure_injection.py",
            "test_a_failing_migration_role_preflight_stops_before_the_migration",
        ),
        (
            "tests/unit/test_deployment_foundation_failure_injection.py",
            "test_a_failing_product_preflight_hook_refuses_before_mutation",
        ),
        (
            "tests/unit/test_deployment_foundation_failure_injection.py",
            "test_a_gate_failure_still_reports_the_world_as_UNTOUCHED",
        ),
        (
            "tests/unit/test_deployment_foundation_failure_injection.py",
            "test_a_loose_mapping_from_an_effects_implementation_is_refused",
        ),
        (
            "tests/unit/test_deployment_foundation_failure_injection.py",
            "test_a_manifest_that_hashes_to_something_else_is_refused_BEFORE_any_mutation",
        ),
        (
            "tests/unit/test_deployment_foundation_failure_injection.py",
            "test_a_migration_failure_that_is_not_lock_contention_is_not_retried",
        ),
        (
            "tests/unit/test_deployment_foundation_failure_injection.py",
            "test_a_migration_that_fails_partway_reports_the_world_as_MUTATED",
        ),
        (
            "tests/unit/test_deployment_foundation_failure_injection.py",
            "test_a_missing_image_digest_refuses_before_anything_is_mutated",
        ),
        (
            "tests/unit/test_deployment_foundation_failure_injection.py",
            "test_a_missing_migration_head_is_caught_even_though_the_command_exited_zero",
        ),
        (
            "tests/unit/test_deployment_foundation_failure_injection.py",
            "test_a_restarting_role_is_a_failed_deployment_not_a_healthy_one",
        ),
        (
            "tests/unit/test_deployment_foundation_failure_injection.py",
            "test_a_revision_with_no_release_evidence_is_refused",
        ),
        (
            "tests/unit/test_deployment_foundation_failure_injection.py",
            "test_a_role_left_on_a_previous_digest_after_the_switch_is_caught",
        ),
        (
            "tests/unit/test_deployment_foundation_failure_injection.py",
            "test_a_rollback_annotates_itself",
        ),
        (
            "tests/unit/test_deployment_foundation_failure_injection.py",
            "test_a_scheduler_that_has_never_ticked_is_distinguished_from_a_late_one",
        ),
        (
            "tests/unit/test_deployment_foundation_failure_injection.py",
            "test_a_source_bind_mount_into_a_container_is_refused",
        ),
        (
            "tests/unit/test_deployment_foundation_failure_injection.py",
            "test_a_stale_scheduler_fails_the_deployment",
        ),
        (
            "tests/unit/test_deployment_foundation_failure_injection.py",
            "test_a_start_annotation_is_sent_BEFORE_the_work",
        ),
        (
            "tests/unit/test_deployment_foundation_failure_injection.py",
            "test_a_successful_run_annotates_start_then_success",
        ),
        (
            "tests/unit/test_deployment_foundation_failure_injection.py",
            "test_an_annotation_sink_failure_never_fails_the_deployment",
        ),
        (
            "tests/unit/test_deployment_foundation_failure_injection.py",
            "test_an_evidence_write_failure_does_not_mask_the_real_failure",
        ),
        (
            "tests/unit/test_deployment_foundation_failure_injection.py",
            "test_an_image_built_from_the_wrong_revision_is_refused",
        ),
        (
            "tests/unit/test_deployment_foundation_failure_injection.py",
            "test_an_image_with_no_revision_label_is_refused",
        ),
        (
            "tests/unit/test_deployment_foundation_failure_injection.py",
            "test_an_unhealthy_worker_fails_the_deployment_even_though_its_container_is_up",
        ),
        (
            "tests/unit/test_deployment_foundation_failure_injection.py",
            "test_an_unreadable_manifest_is_a_refusal_and_not_a_match",
        ),
        (
            "tests/unit/test_deployment_foundation_failure_injection.py",
            "test_an_untracked_compose_override_is_refused",
        ),
        (
            "tests/unit/test_deployment_foundation_failure_injection.py",
            "test_evidence_is_written_before_success_is_declared",
        ),
        (
            "tests/unit/test_deployment_foundation_failure_injection.py",
            "test_evidence_is_written_even_when_the_run_dies_at_the_migration",
        ),
        (
            "tests/unit/test_deployment_foundation_failure_injection.py",
            "test_image_retention_failure_does_not_fail_a_verified_deployment",
        ),
        (
            "tests/unit/test_deployment_foundation_failure_injection.py",
            "test_migration_lock_contention_is_retried_and_then_gives_up",
        ),
        (
            "tests/unit/test_deployment_foundation_failure_injection.py",
            "test_missing_migration_credentials_are_refused_before_ddl",
        ),
        (
            "tests/unit/test_deployment_foundation_failure_injection.py",
            "test_rollback_actually_restores_the_previous_digest",
        ),
        (
            "tests/unit/test_deployment_foundation_failure_injection.py",
            "test_rollback_is_REFUSED_for_a_maintenance_required_release",
        ),
        (
            "tests/unit/test_deployment_foundation_failure_injection.py",
            "test_sql_that_sets_lock_timeout_does_not_make_a_permission_error_retryable",
        ),
        (
            "tests/unit/test_deployment_foundation_failure_injection.py",
            "test_the_verifier_receives_the_real_checksum_and_size",
        ),
        (
            "tests/unit/test_deployment_foundation_bootstrap_invocation.py",
            "test_a_bootstrap_marks_the_run_MUTATED_before_the_call",
        ),
        (
            "tests/unit/test_deployment_foundation_bootstrap_invocation.py",
            "test_a_provider_failure_is_a_FAILURE",
        ),
        (
            "tests/unit/test_deployment_foundation_bootstrap_invocation.py",
            "test_a_refused_compare_and_set_is_a_REFUSAL_not_a_failure",
        ),
        (
            "tests/unit/test_deployment_foundation_bootstrap_invocation.py",
            "test_a_v1_plan_bootstraps_nothing_and_records_nothing",
        ),
        (
            "tests/unit/test_deployment_foundation_bootstrap_invocation.py",
            "test_any_other_standing_is_REFUSED_as_ambiguous",
        ),
        (
            "tests/unit/test_deployment_foundation_bootstrap_invocation.py",
            "test_both_histories_are_accepted_and_land_in_the_document",
        ),
        (
            "tests/unit/test_deployment_foundation_bootstrap_invocation.py",
            "test_each_authorized_bootstrap_is_invoked_once",
        ),
        (
            "tests/unit/test_deployment_foundation_bootstrap_invocation.py",
            "test_it_runs_BEFORE_the_step_loop",
        ),
        (
            "tests/unit/test_deployment_foundation_bootstrap_invocation.py",
            "test_no_exception_text_reaches_the_document_on_the_bootstrap_path",
        ),
        (
            "tests/unit/test_deployment_foundation_bootstrap_invocation.py",
            "test_the_bootstraps_come_from_the_PLAN_and_nowhere_else",
        ),
        (
            "tests/unit/test_deployment_foundation_deployment_evidence.py",
            "test_a_refusal_and_a_failure_are_DIFFERENT_standings",
        ),
        (
            "tests/unit/test_deployment_foundation_deployment_evidence.py",
            "test_a_refused_run_mutated_nothing",
        ),
        (
            "tests/unit/test_deployment_foundation_deployment_evidence.py",
            "test_a_refused_run_records_a_standing_and_no_exception_text",
        ),
        (
            "tests/unit/test_deployment_foundation_deployment_evidence.py",
            "test_the_diagnostic_survives_in_process_even_though_it_does_not_travel",
        ),
    }
)
