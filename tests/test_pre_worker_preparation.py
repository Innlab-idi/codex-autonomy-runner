from dataclasses import FrozenInstanceError
from pathlib import Path
import unittest

from codex_autonomy_runner.execution_baseline import (
    ExecutionBaselineResolution,
    ExecutionBaselineStatus,
    IntendedRefObservation,
    resolve_execution_baseline,
)
from codex_autonomy_runner.existing_work import (
    ExistingWorkObservation,
    discover_existing_work,
)
from codex_autonomy_runner.invocation_contract import InvocationRequest
from codex_autonomy_runner.pre_worker_preparation import (
    PreWorkerPreparationViolation,
    validate_pre_worker_preparation,
)
from codex_autonomy_runner.repository_inspection import ChangedPaths, RepositoryInspection


CHECKPOINT = "CORE-09"
HEAD = "a" * 40


def inspection(head=HEAD, branch="prepared-branch", detached=False, paths=None):
    return RepositoryInspection(
        root=Path("repository"),
        head_sha=head,
        branch=branch,
        is_detached=detached,
        changed_paths=ChangedPaths((), (), ()) if paths is None else paths,
    )


def new_work_resolution(head=HEAD):
    return resolve_execution_baseline(
        CHECKPOINT,
        InvocationRequest(repository=Path("repository"), intended_ref="origin/main"),
        IntendedRefObservation(ref="origin/main", head_sha=head),
        discover_existing_work(CHECKPOINT, ()),
    )


def existing_work_resolution(head=HEAD, branch="codex/core-09"):
    observed = ExistingWorkObservation(
        checkpoint_id=CHECKPOINT,
        pr_number=9,
        branch=branch,
        head_sha=head,
        is_active=True,
    )
    return resolve_execution_baseline(
        CHECKPOINT,
        InvocationRequest(repository=Path("repository"), intended_ref="origin/main"),
        IntendedRefObservation(ref="origin/main", head_sha="intended-head"),
        discover_existing_work(CHECKPOINT, (observed,)),
    )


class PreWorkerPreparationTests(unittest.TestCase):
    def validate(self, resolution=None, prepared=None):
        return validate_pre_worker_preparation(
            new_work_resolution() if resolution is None else resolution,
            inspection() if prepared is None else prepared,
        )

    def test_clean_attached_new_work_at_exact_head_is_valid_without_branch_name_rule(self):
        result = self.validate(prepared=inspection(branch="a-different-new-work-branch"))

        self.assertTrue(result.is_valid)
        self.assertEqual((), result.violations)

    def test_clean_existing_work_at_exact_reconciled_branch_and_head_is_valid(self):
        result = self.validate(
            resolution=existing_work_resolution(),
            prepared=inspection(branch="codex/core-09"),
        )

        self.assertTrue(result.is_valid)
        self.assertEqual((), result.violations)

    def test_ambiguous_and_input_mismatch_resolutions_are_unresolved(self):
        ambiguous = resolve_execution_baseline(
            CHECKPOINT,
            InvocationRequest(repository=Path("repository"), intended_ref="origin/main"),
            IntendedRefObservation(ref="origin/main", head_sha=HEAD),
            discover_existing_work(
                CHECKPOINT,
                (
                    ExistingWorkObservation(CHECKPOINT, 1, "one", HEAD, True),
                    ExistingWorkObservation(CHECKPOINT, 2, "two", HEAD, True),
                ),
            ),
        )
        mismatch = resolve_execution_baseline(
            CHECKPOINT,
            InvocationRequest(repository=Path("repository"), intended_ref="origin/Main"),
            IntendedRefObservation(ref="origin/main", head_sha=HEAD),
            discover_existing_work(CHECKPOINT, ()),
        )

        for resolution in (ambiguous, mismatch):
            with self.subTest(status=resolution.status):
                result = self.validate(resolution=resolution)
                self.assertEqual(
                    (PreWorkerPreparationViolation.BASELINE_UNRESOLVED,), result.violations
                )

    def test_resolved_status_without_a_baseline_is_unresolved(self):
        malformed = ExecutionBaselineResolution(
            status=ExecutionBaselineStatus.NEW_WORK,
            baseline=None,
        )

        result = self.validate(resolution=malformed)

        self.assertEqual((PreWorkerPreparationViolation.BASELINE_UNRESOLVED,), result.violations)

    def test_each_preexisting_change_area_makes_prepared_tree_dirty(self):
        for paths in (
            ChangedPaths(("staged.py",), (), ()),
            ChangedPaths((), ("unstaged.py",), ()),
            ChangedPaths((), (), ("untracked.py",)),
        ):
            with self.subTest(paths=paths):
                result = self.validate(prepared=inspection(paths=paths))
                self.assertEqual(
                    (PreWorkerPreparationViolation.PREPARED_TREE_DIRTY,), result.violations
                )

    def test_head_mismatch_is_exact_and_case_sensitive(self):
        result = self.validate(prepared=inspection(head="A" * 40))

        self.assertEqual((PreWorkerPreparationViolation.HEAD_MISMATCH,), result.violations)

    def test_existing_work_requires_exact_case_sensitive_reconciled_branch(self):
        result = self.validate(
            resolution=existing_work_resolution(branch="Feature/Exact"),
            prepared=inspection(branch="feature/exact"),
        )

        self.assertEqual(
            (PreWorkerPreparationViolation.EXISTING_BRANCH_MISMATCH,), result.violations
        )

    def test_detached_new_and_existing_work_are_invalid(self):
        for resolution in (new_work_resolution(), existing_work_resolution()):
            with self.subTest(status=resolution.status):
                result = self.validate(resolution=resolution, prepared=inspection(detached=True))
                self.assertIn(PreWorkerPreparationViolation.DETACHED_HEAD, result.violations)

    def test_missing_branch_is_reported_separately_from_detached_state(self):
        result = self.validate(prepared=inspection(branch=None, detached=True))

        self.assertEqual(
            (
                PreWorkerPreparationViolation.DETACHED_HEAD,
                PreWorkerPreparationViolation.PREPARED_BRANCH_MISSING,
            ),
            result.violations,
        )

    def test_dirty_head_mismatch_detached_and_existing_branch_mismatch_coexist_in_order(self):
        result = self.validate(
            resolution=existing_work_resolution(branch="codex/core-09"),
            prepared=inspection(
                head="b" * 40,
                branch="other",
                detached=True,
                paths=ChangedPaths(("staged.py",), (), ()),
            ),
        )

        self.assertEqual(
            (
                PreWorkerPreparationViolation.PREPARED_TREE_DIRTY,
                PreWorkerPreparationViolation.HEAD_MISMATCH,
                PreWorkerPreparationViolation.DETACHED_HEAD,
                PreWorkerPreparationViolation.EXISTING_BRANCH_MISMATCH,
            ),
            result.violations,
        )

    def test_result_and_violations_are_immutable(self):
        result = self.validate()

        self.assertIsInstance(result.violations, tuple)
        with self.assertRaises(FrozenInstanceError):
            result.violations = ()


if __name__ == "__main__":
    unittest.main()
