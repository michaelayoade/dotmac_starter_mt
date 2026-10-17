"""Hosted contract coverage for the additive learning package surface."""

import dotmac_learning as learning
from dotmac_learning import (
    assessment_contracts,
    curriculum,
    curriculum_transfer,
    curriculum_transfer_contracts,
    exam_engine,
    grading,
    reading,
    references,
    transfer,
    transfer_contracts,
)

LEGACY_PUBLIC_NAMES = (
    "ASSESSMENT_MODES",
    "EVENT_KINDS",
    "KIND_ACTIVITY_STARTED",
    "KIND_CERTIFICATE_EARNED",
    "KIND_CHAPTER_COMPLETED",
    "KIND_CHAPTER_VIEWED",
    "KIND_COURSE_VIEWED",
    "KIND_LAB_CHECK_FAILED",
    "KIND_LAB_CHECK_PASSED",
    "KIND_LAB_LAUNCHED",
    "KIND_SESSION_ATTENDED",
    "KIND_SUBMISSION_MADE",
    "KIND_WORK_GRADED",
    "ActivityFact",
    "ActivityRowV1",
    "ActivityWrite",
    "AdmissionContention",
    "AdmissionDecision",
    "AdmissionObservation",
    "AdmissionRefused",
    "AttemptAdmissionPolicy",
    "CertificateCommand",
    "CertificateFact",
    "CertificateIssue",
    "CertificateNotEligible",
    "CertificateRowV1",
    "CompletionCommand",
    "CompletionFact",
    "CompletionResult",
    "CourseCompletionRowV1",
    "CourseFact",
    "CourseRowV1",
    "CourseWrite",
    "DestinationNotEmpty",
    "EventEmission",
    "EventFact",
    "EventWrite",
    "Float8Bits",
    "GradedResult",
    "ImportObservation",
    "JsonbText",
    "LabResultCommand",
    "LearningEventRowV1",
    "LearningSnapshotV1",
    "MCQResultCommand",
    "OverrideScoreCommand",
    "ReferenceNotFound",
    "ResultBadRequest",
    "ResultWrite",
    "RowCountObservation",
    "RowMismatch",
    "ScoreFact",
    "ScoreRowV1",
    "SnapshotMismatch",
    "SnapshotValidationError",
    "SubmissionFact",
    "SubmissionRowV1",
    "TransactionChanged",
    "TransferConnectionError",
    "UnsupportedFloatRepresentation",
    "UnsupportedTimestampRepresentation",
    "VerificationObservation",
    "WeekBucket",
    "__version__",
    "active_person_ids",
    "best_scores_by_course_for_person",
    "best_scores_for",
    "best_scores_for_person",
    "create_activity",
    "create_course",
    "emit",
    "export_snapshot",
    "get_activity",
    "get_course",
    "import_snapshot",
    "issue_certificate",
    "kind_counts",
    "last_activity_at",
    "module",
    "override_score",
    "recompute_completion",
    "record",
    "record_lab_result",
    "record_mcq_result",
    "update_activity",
    "update_course",
    "versions_dir",
    "verify_snapshot",
    "weekly_buckets",
)


def test_existing_public_surface_and_transfer_v1_remain_available():
    assert set(LEGACY_PUBLIC_NAMES) <= set(learning.__all__)
    assert len(learning.__all__) == len(set(learning.__all__))
    for name in learning.__all__:
        assert hasattr(learning, name), name
    assert learning.create_course is references.create_course
    assert learning.create_draft_course is curriculum.create_course
    assert learning.create_draft_course is not learning.create_course
    assert learning.mark_subtopic_complete is reading.mark_complete
    assert learning.annotation_state is reading.get_state
    assert learning.list_annotations is reading.list_for_person


def test_curriculum_and_reading_commands_have_public_typed_contracts():
    required = (
        "AnnotatedChapter",
        "AnnotationCommand",
        "AnnotationGroup",
        "AnnotationState",
        "BankDocument",
        "BankFact",
        "BankQuestion",
        "ChapterFact",
        "ChapterReadWrite",
        "ChapterReadingCommand",
        "ChapterSource",
        "ChapterSubtopicCommand",
        "ChapterWrite",
        "CourseCreate",
        "CurriculumConflict",
        "CurriculumNotFound",
        "FigureSource",
        "Heading",
        "ImportedChapter",
        "ManualImport",
        "NoteCommand",
        "NoteFact",
        "PublicCatalogItem",
        "ReadingChapterFact",
        "ReadingCourseFact",
        "SubtopicCompletion",
        "SubtopicFact",
        "SubtopicReadCommand",
        "annotation_state",
        "chapter_source_hash",
        "chapter_subtopics",
        "complete_subtopic",
        "completed_chapter_ids",
        "completed_slugs",
        "completed_slugs_by_chapter",
        "create_draft_course",
        "delete_chapter",
        "doc_from_db",
        "import_manual",
        "lint_bank",
        "list_annotations",
        "load_bank",
        "mark_subtopic_complete",
        "public_catalog",
        "save_note",
        "slugify_heading",
        "toggle_bookmark",
        "toggle_chapter_read",
        "upsert_chapter",
    )
    assert set(required) <= set(learning.__all__)
    for name in required:
        assert callable(getattr(learning, name)), name


def test_public_migration_directory_includes_both_canonical_learning_revisions():
    # In source CI this directory belongs to the editable source candidate;
    # isolated-wheel execution remains a separate release qualification gate.
    names = {path.name for path in learning.versions_dir().glob("*.py")}
    assert {"lr_0001_learning.py", "lr_0002_curriculum_reading.py"} <= names


def test_v1_and_v2_transfer_surfaces_remain_distinct_and_bound_to_their_owners():
    assert learning.export_snapshot is transfer.export_snapshot
    assert learning.import_snapshot is transfer.import_snapshot
    assert learning.verify_snapshot is transfer.verify_snapshot
    assert learning.LearningSnapshotV1 is transfer_contracts.LearningSnapshotV1
    assert learning.export_snapshot_v2 is curriculum_transfer.export_snapshot_v2
    assert learning.import_snapshot_v2 is curriculum_transfer.import_snapshot_v2
    assert learning.verify_snapshot_v2 is curriculum_transfer.verify_snapshot_v2
    assert (
        learning.LearningSnapshotV2 is curriculum_transfer_contracts.LearningSnapshotV2
    )
    assert learning.LearningSnapshotV2 is not learning.LearningSnapshotV1


def test_existing_exam_and_grading_engine_have_one_public_owner():
    assert learning.ExamQuestion is exam_engine.Question
    assert learning.ExamSitter is exam_engine.Sitter
    assert learning.SelectionPolicy is exam_engine.SelectionPolicy
    assert learning.GradingQuestion is assessment_contracts.GradingQuestion
    assert learning.select_questions is exam_engine.select
    assert learning.present_options is exam_engine.present_options
    assert learning.chance_baseline is exam_engine.chance_baseline
    assert learning.near_chance_floor is exam_engine.near_chance_floor
    assert learning.duration_floor is exam_engine.duration_floor
    assert learning.check_validity is exam_engine.check_validity
    assert learning.grade_submission is grading.grade_submission
    assert grading.GradedResult is learning.GradedResult


def test_participation_commands_and_access_keep_their_public_owner():
    from dotmac_learning import (
        audience,
        entitlements,
        participation_contracts,
        roster,
        tracks,
    )

    owners = (
        (
            participation_contracts,
            (
                "ActivateEnrollmentCommand",
                "ActivationResult",
                "AssignFacilitatorCommand",
                "AudienceCounts",
                "BulkEnrollCommand",
                "BulkEnrollResult",
                "ClassificationCounts",
                "ClassifyRosterCommand",
                "EnrollmentFact",
                "FacilitatorEligibility",
                "ParticipationBadRequest",
                "ParticipationForbidden",
                "ParticipationNotFound",
                "RosterAuthority",
                "RosterStateCommand",
                "UnclassifiedPerson",
            ),
        ),
        (
            roster,
            (
                "activate_enrollment",
                "assign_facilitator",
                "bulk_enroll",
                "set_roster_state",
            ),
        ),
        (
            audience,
            (
                "classify_from_roster",
                "counts_by_audience",
                "staff_person_ids",
                "unclassified",
            ),
        ),
        (
            tracks,
            (
                "CreateTrackCommand",
                "AddTrackCoursesCommand",
                "ReorderTrackCoursesCommand",
                "TrackFact",
                "CohortTrackFact",
                "CohortTrackItem",
                "list_cohort_tracks",
                "tracks_for_cohorts",
                "create_cohort_track",
                "add_courses_to_cohort_track",
                "reorder_track_courses",
                "cohort_track_or_404",
                "ensure_track_offerings",
                "assign_enrollment_track",
                "clear_enrollment_track",
                "default_track_for_cohort",
                "ensure_default_track_for_cohort",
            ),
        ),
        (
            entitlements,
            (
                "CourseAccessDenied",
                "CourseAccessState",
                "visible_course_ids",
                "course_access_states",
                "accessible_course_ids",
                "person_can_access_course",
                "require_course_access",
                "open_course_ids",
                "unmet_prerequisites",
                "require_course_open",
            ),
        ),
    )
    for owner, names in owners:
        for name in names:
            assert name in learning.__all__
            assert getattr(learning, name) is getattr(owner, name)


def test_twenty_one_table_v3_transfer_keeps_its_public_owner():
    from dotmac_learning import participation_transfer, participation_transfer_contracts

    owners = (
        (
            participation_transfer,
            (
                "export_snapshot_v3",
                "import_snapshot_v3",
                "verify_snapshot_v3",
            ),
        ),
        (
            participation_transfer_contracts,
            (
                "CohortRowV3",
                "EnrollmentRowV3",
                "TrackRowV3",
                "TrackCourseRowV3",
                "CohortTrackRowV3",
                "CourseOfferingRowV3",
                "CoursePrerequisiteRowV3",
                "ParticipationStorageRowV3",
                "LearningSnapshotV3",
                "SnapshotClosureErrorV3",
                "VerificationObservationV3",
                "ImportObservationV3",
            ),
        ),
    )
    for owner, names in owners:
        for name in names:
            assert name in learning.__all__
            assert getattr(learning, name) is getattr(owner, name)
