from dataclasses import FrozenInstanceError
from pathlib import Path
import unittest

from codex_autonomy_runner.changed_path_validation import ChangedPathValidation
from codex_autonomy_runner.repository_inspection import ChangedPaths, RepositoryInspection
from codex_autonomy_runner.worker_boundary_validation import (
    WorkerBoundaryViolation,
    validate_worker_boundary,
)


def inspection(root=Path("C:/repository"), head="a" * 40, branch="main", detached=False, paths=None):
    return RepositoryInspection(
        root=root,
        head_sha=head,
        branch=branch,
        is_detached=detached,
        changed_paths=ChangedPaths((), (), ()) if paths is None else paths,
    )


def validation(actual=(), unexpected=()):
    permitted = tuple(path for path in actual if path not in unexpected)
    return ChangedPathValidation(
        actual_paths=tuple(actual),
        permitted_paths=permitted,
        unexpected_paths=tuple(unexpected),
    )


class WorkerBoundaryValidationTests(unittest.TestCase):
    def validate(self, pre=None, post=None, paths=None):
        pre = inspection() if pre is None else pre
        post = inspection() if post is None else post
        paths = validation() if paths is None else paths
        return validate_worker_boundary(pre, post, paths)

    def test_clean_stable_snapshots_and_valid_paths_are_valid(self):
        result = self.validate(
            post=inspection(paths=ChangedPaths(("a.py",), (), ("new.txt",))),
            paths=validation(("a.py", "new.txt")),
        )

        self.assertTrue(result.is_valid)
        self.assertEqual((), result.violations)

    def test_each_pre_worker_change_area_is_dirty(self):
        for paths in (
            ChangedPaths(("staged.py",), (), ()),
            ChangedPaths((), ("unstaged.py",), ()),
            ChangedPaths((), (), ("untracked.py",)),
        ):
            with self.subTest(paths=paths):
                result = self.validate(pre=inspection(paths=paths))
                self.assertEqual((WorkerBoundaryViolation.PRE_WORKER_DIRTY,), result.violations)

    def test_each_git_identity_change_has_its_explicit_violation(self):
        cases = (
            (inspection(root=Path("C:/other")), WorkerBoundaryViolation.REPOSITORY_ROOT_CHANGED),
            (inspection(head="A" * 40), WorkerBoundaryViolation.HEAD_CHANGED),
            (inspection(branch="other"), WorkerBoundaryViolation.BRANCH_CHANGED),
            (inspection(detached=True), WorkerBoundaryViolation.DETACHED_STATE_CHANGED),
        )
        for post, expected in cases:
            with self.subTest(expected=expected):
                self.assertIn(expected, self.validate(post=post).violations)

    def test_multiple_identity_changes_preserve_fixed_violation_order(self):
        result = self.validate(
            pre=inspection(paths=ChangedPaths(("old.py",), (), ())),
            post=inspection(root=Path("C:/other"), head="b" * 40, branch="other", detached=True),
        )

        self.assertEqual(
            (
                WorkerBoundaryViolation.PRE_WORKER_DIRTY,
                WorkerBoundaryViolation.REPOSITORY_ROOT_CHANGED,
                WorkerBoundaryViolation.HEAD_CHANGED,
                WorkerBoundaryViolation.BRANCH_CHANGED,
                WorkerBoundaryViolation.DETACHED_STATE_CHANGED,
            ),
            result.violations,
        )

    def test_head_and_branch_matching_are_exact_and_case_sensitive(self):
        result = self.validate(post=inspection(head="A" * 40, branch="MAIN"))

        self.assertIn(WorkerBoundaryViolation.HEAD_CHANGED, result.violations)
        self.assertIn(WorkerBoundaryViolation.BRANCH_CHANGED, result.violations)

    def test_post_paths_must_bind_exactly_to_validation_actual_paths(self):
        post = inspection(paths=ChangedPaths(("a.py",), (), ("new.txt",)))

        matching = self.validate(post=post, paths=validation(("a.py", "new.txt")))
        mismatch = self.validate(post=post, paths=validation(("a.py",)))

        self.assertNotIn(WorkerBoundaryViolation.POST_PATHS_MISMATCH, matching.violations)
        self.assertIn(WorkerBoundaryViolation.POST_PATHS_MISMATCH, mismatch.violations)

    def test_untracked_and_duplicate_paths_participate_in_binding_once(self):
        post = inspection(paths=ChangedPaths(("same.py",), ("same.py",), ("same.py", "new.txt")))

        result = self.validate(post=post, paths=validation(("new.txt", "same.py")))

        self.assertTrue(result.is_valid)

    def test_invalid_core_six_validation_reports_unauthorized_paths(self):
        result = self.validate(
            post=inspection(paths=ChangedPaths(("unexpected.py",), (), ())),
            paths=validation(("unexpected.py",), ("unexpected.py",)),
        )

        self.assertEqual((WorkerBoundaryViolation.UNAUTHORIZED_POST_PATHS,), result.violations)

    def test_path_mismatch_and_unauthorized_paths_can_coexist(self):
        post = inspection(paths=ChangedPaths((), (), ("actual.py",)))
        result = self.validate(post=post, paths=validation(("other.py",), ("other.py",)))

        self.assertEqual(
            (
                WorkerBoundaryViolation.POST_PATHS_MISMATCH,
                WorkerBoundaryViolation.UNAUTHORIZED_POST_PATHS,
            ),
            result.violations,
        )

    def test_no_worker_changes_can_be_valid(self):
        self.assertTrue(self.validate().is_valid)

    def test_result_and_violations_are_immutable(self):
        result = self.validate()

        self.assertIsInstance(result.violations, tuple)
        with self.assertRaises(FrozenInstanceError):
            result.violations = ()


if __name__ == "__main__":
    unittest.main()
