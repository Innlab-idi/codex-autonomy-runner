"""Tests for pure allowlisted finalizer closure derivation and validation."""

from dataclasses import FrozenInstanceError, replace
import unittest

from codex_autonomy_runner.changed_path_validation import validate_changed_paths
from codex_autonomy_runner.finalizer_closure import (
    ALLOWLISTED_CLOSURE_PATH,
    FinalizerClosurePlan,
    FinalizerClosureResult,
    FinalizerClosureViolation,
    derive_allowlisted_closure,
    validate_allowlisted_closure,
)
from codex_autonomy_runner.finalizer_eligibility import (
    EligibleFinalizerUnit,
    FinalizerEligibility,
    FinalizerEligibilityPath,
    FinalizerEligibilityViolation,
)
from codex_autonomy_runner.repository_inspection import ChangedPaths


class FinalizerClosureTests(unittest.TestCase):
    def setUp(self):
        self.unit = EligibleFinalizerUnit(
            "repo-A", "FINALIZER-02", 27, "codex/finalizer-02", "substantive-head", "substantive-head"
        )
        self.eligibility = FinalizerEligibility(
            FinalizerEligibilityPath.CLOSURE_REQUIRED, (), self.unit
        )
        self.row = (
            "| FINALIZER-02 | AI | AI_REVIEW | closure scope | "
            "ready for review. |\n"
        )
        self.source = (
            "# Work queue\n\n"
            "| Checkpoint | Gate | State | Scope | Dependency / next action |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| OTHER | AI | DONE | other scope | untouched |\n"
            + self.row
            + "\n## FINALIZER phase checkpoint plan\n"
            "### FINALIZER-02 — Pure allowlisted closure derivation and validation\n"
        )
        derived = derive_allowlisted_closure(self.eligibility, self.source)
        self.assertTrue(derived.is_valid)
        self.plan = derived.plan

    def test_happy_path_derives_only_target_row_and_metadata(self):
        self.assertEqual(self.plan.path, ALLOWLISTED_CLOSURE_PATH)
        self.assertEqual(self.plan.unit, self.unit)
        self.assertIn("| FINALIZER-02 | AI | DONE | closure scope |", self.plan.result_work_queue)
        self.assertIn("AI SUPERVISOR approved substantive HEAD substantive-head; closure materialized.", self.plan.result_work_queue)
        self.assertIn("| OTHER | AI | DONE | other scope | untouched |\n", self.plan.result_work_queue)
        self.assertIn("### FINALIZER-02 — Pure allowlisted closure derivation and validation\n", self.plan.result_work_queue)

    def test_source_and_result_preserve_all_newline_forms(self):
        for source, ending in (
            (self.source, "\n"),
            (self.source.replace("\n", "\r\n"), "\r\n"),
            (self.source[:-1], ""),
        ):
            result = derive_allowlisted_closure(self.eligibility, source)
            self.assertTrue(result.is_valid)
            if ending:
                self.assertTrue(result.plan.source_work_queue.endswith(ending))
                self.assertTrue(result.plan.result_work_queue.endswith(ending))
            else:
                self.assertFalse(result.plan.source_work_queue.endswith(("\n", "\r")))
                self.assertFalse(result.plan.result_work_queue.endswith(("\n", "\r")))

    def test_only_closure_required_eligibility_is_accepted(self):
        for path in (FinalizerEligibilityPath.INELIGIBLE, FinalizerEligibilityPath.PROTECTED_MERGE):
            result = derive_allowlisted_closure(
                FinalizerEligibility(path, (), self.unit), self.source
            )
            self.assertEqual(result.violations, (FinalizerClosureViolation.ELIGIBILITY_NOT_CLOSURE_REQUIRED,))

    def test_inconsistent_closure_required_eligibility_is_rejected(self):
        cases = (
            FinalizerEligibility(FinalizerEligibilityPath.CLOSURE_REQUIRED, (FinalizerEligibilityViolation.NOT_MERGEABLE,), self.unit),
            FinalizerEligibility(FinalizerEligibilityPath.CLOSURE_REQUIRED, (), None),
            FinalizerEligibility(
                FinalizerEligibilityPath.CLOSURE_REQUIRED,
                (),
                replace(self.unit, current_head_sha="other-head"),
            ),
        )
        for eligibility in cases:
            with self.subTest(eligibility=eligibility):
                result = derive_allowlisted_closure(eligibility, self.source)
                self.assertEqual(result.violations, (FinalizerClosureViolation.ELIGIBILITY_INCONSISTENT,))

    def test_target_row_must_exist_once_and_be_ai_review(self):
        for source, violation in (
            (self.source.replace(self.row, ""), FinalizerClosureViolation.TARGET_ROW_NOT_FOUND),
            (self.source + self.row, FinalizerClosureViolation.TARGET_ROW_DUPLICATE),
            (self.source.replace("| FINALIZER-02 | AI | AI_REVIEW |", "| FINALIZER-02 | HUMAN | AI_REVIEW |"), FinalizerClosureViolation.TARGET_ROW_MALFORMED),
            (self.source.replace("| FINALIZER-02 | AI | AI_REVIEW |", "| FINALIZER-02 | AI | DONE |"), FinalizerClosureViolation.TARGET_ROW_MALFORMED),
        ):
            with self.subTest(violation=violation):
                result = derive_allowlisted_closure(self.eligibility, source)
                self.assertEqual(result.violations, (violation,))

    def test_wrong_checkpoint_does_not_close_another_row(self):
        result = derive_allowlisted_closure(
            replace(
                self.eligibility,
                unit=replace(self.unit, checkpoint_id="FINALIZER-99"),
            ),
            self.source,
        )
        self.assertEqual(result.violations, (FinalizerClosureViolation.TARGET_ROW_NOT_FOUND,))

    def test_validation_accepts_exact_result_and_core06_path(self):
        for paths in (
            ChangedPaths((ALLOWLISTED_CLOSURE_PATH,), (), ()),
            ChangedPaths((), (ALLOWLISTED_CLOSURE_PATH,), ()),
            ChangedPaths((), (), (ALLOWLISTED_CLOSURE_PATH,)),
        ):
            with self.subTest(paths=paths):
                result = validate_allowlisted_closure(
                    self.plan, self.plan.result_work_queue, paths
                )
                self.assertTrue(result.is_valid)
                self.assertEqual(result.plan, self.plan)

    def test_validation_rejects_duplicate_allowed_path_occurrences(self):
        for paths in (
            ChangedPaths((ALLOWLISTED_CLOSURE_PATH, ALLOWLISTED_CLOSURE_PATH), (), ()),
            ChangedPaths((ALLOWLISTED_CLOSURE_PATH,), (ALLOWLISTED_CLOSURE_PATH,), ()),
            ChangedPaths(
                (ALLOWLISTED_CLOSURE_PATH,),
                (ALLOWLISTED_CLOSURE_PATH,),
                (ALLOWLISTED_CLOSURE_PATH,),
            ),
        ):
            with self.subTest(paths=paths):
                result = validate_allowlisted_closure(
                    self.plan, self.plan.result_work_queue, paths
                )
                self.assertIn(
                    FinalizerClosureViolation.OBSERVED_PATHS_DUPLICATE,
                    result.violations,
                )
                self.assertFalse(result.is_valid)

    def test_validation_rejects_unexpected_or_missing_paths(self):
        for paths in (
            ChangedPaths(("other.txt",), (), ()),
            ChangedPaths((ALLOWLISTED_CLOSURE_PATH, "other.txt"), (), ()),
            ChangedPaths((), (), ()),
            ChangedPaths(("docs/work_queue.md",), (), ()),
            ChangedPaths(("a/../docs/WORK_QUEUE.md",), (), ()),
        ):
            with self.subTest(paths=paths):
                result = validate_allowlisted_closure(self.plan, self.plan.result_work_queue, paths)
                self.assertIn(FinalizerClosureViolation.OBSERVED_PATHS_MISMATCH, result.violations)

    def test_validation_rejects_noop_and_any_content_change(self):
        noop = validate_allowlisted_closure(self.plan, self.plan.source_work_queue)
        self.assertIn(FinalizerClosureViolation.CANDIDATE_NOOP, noop.violations)
        changed = self.plan.result_work_queue.replace("other scope", "changed scope")
        self.assertIn(
            FinalizerClosureViolation.CANDIDATE_CONTENT_MISMATCH,
            validate_allowlisted_closure(self.plan, changed).violations,
        )
        whitespace = self.plan.result_work_queue.replace("# Work queue", "#  Work queue")
        self.assertIn(
            FinalizerClosureViolation.CANDIDATE_CONTENT_MISMATCH,
            validate_allowlisted_closure(self.plan, whitespace).violations,
        )

    def test_validation_rejects_semantic_target_changes(self):
        for replacement in (
            ("| FINALIZER-02 | AI | DONE |", "| FINALIZER-02 | HUMAN | DONE |"),
            ("| FINALIZER-02 | AI | DONE | closure scope |", "| FINALIZER-02 | AI | DONE | changed scope |"),
            ("| FINALIZER-02 | AI | DONE |", "| OTHER | AI | DONE |"),
        ):
            candidate = self.plan.result_work_queue.replace(*replacement)
            self.assertIn(
                FinalizerClosureViolation.CANDIDATE_CONTENT_MISMATCH,
                validate_allowlisted_closure(self.plan, candidate).violations,
            )

    def test_plan_and_results_are_immutable_and_deterministic(self):
        first = derive_allowlisted_closure(self.eligibility, self.source)
        second = derive_allowlisted_closure(self.eligibility, self.source)
        self.assertEqual(first, second)
        with self.assertRaises(FrozenInstanceError):
            self.plan.path = "other"
        with self.assertRaises(FrozenInstanceError):
            first.violations = ()
        self.assertIsInstance(first.violations, tuple)


if __name__ == "__main__":
    unittest.main()
