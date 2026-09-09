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
    ExistingWorkDiscovery,
    ExistingWorkMatch,
    ExistingWorkObservation,
    discover_existing_work,
)
from codex_autonomy_runner.invocation_contract import InvocationRequest


CHECKPOINT = "CORE-08"


def request(intended_ref="origin/main"):
    return InvocationRequest(repository=Path("repository"), intended_ref=intended_ref)


def intended_ref(ref="origin/main", head_sha="a" * 40):
    return IntendedRefObservation(ref=ref, head_sha=head_sha)


def observation(
    checkpoint_id=CHECKPOINT,
    pr_number=8,
    branch="codex/core-08",
    head_sha="b" * 40,
    is_active=True,
):
    return ExistingWorkObservation(
        checkpoint_id=checkpoint_id,
        pr_number=pr_number,
        branch=branch,
        head_sha=head_sha,
        is_active=is_active,
    )


def no_existing_work():
    return discover_existing_work(CHECKPOINT, ())


class ExecutionBaselineTests(unittest.TestCase):
    def test_intended_ref_observation_preserves_exact_values_and_is_immutable(self):
        observed = intended_ref(ref="Refs/Heads/Feature", head_sha="AbC123")

        self.assertEqual("Refs/Heads/Feature", observed.ref)
        self.assertEqual("AbC123", observed.head_sha)
        with self.assertRaises(FrozenInstanceError):
            observed.ref = "other"

    def test_no_existing_work_resolves_new_work_from_exact_intended_ref_observation(self):
        result = resolve_execution_baseline(
            CHECKPOINT,
            request("origin/main"),
            intended_ref("origin/main", "A" * 40),
            no_existing_work(),
        )

        self.assertEqual(ExecutionBaselineStatus.NEW_WORK, result.status)
        self.assertTrue(result.is_resolved)
        self.assertEqual(CHECKPOINT, result.baseline.checkpoint_id)
        self.assertEqual("origin/main", result.baseline.resolved_ref)
        self.assertEqual("A" * 40, result.baseline.expected_head_sha)
        self.assertIsNone(result.baseline.pr_number)

    def test_unique_existing_work_preserves_exact_pr_branch_and_head(self):
        existing = observation(pr_number=41, branch="Feature/Exact Name", head_sha="B" * 40)
        result = resolve_execution_baseline(
            CHECKPOINT,
            request(),
            intended_ref(head_sha="A" * 40),
            discover_existing_work(CHECKPOINT, (existing,)),
        )

        self.assertEqual(ExecutionBaselineStatus.EXISTING_WORK, result.status)
        self.assertTrue(result.is_resolved)
        self.assertEqual(41, result.baseline.pr_number)
        self.assertEqual("Feature/Exact Name", result.baseline.resolved_ref)
        self.assertEqual("B" * 40, result.baseline.expected_head_sha)

    def test_existing_work_head_wins_over_a_different_advanced_intended_ref_head(self):
        existing = observation(head_sha="existing-head")
        result = resolve_execution_baseline(
            CHECKPOINT,
            request(),
            intended_ref(head_sha="advanced-intended-ref-head"),
            discover_existing_work(CHECKPOINT, (existing,)),
        )

        self.assertEqual(ExecutionBaselineStatus.EXISTING_WORK, result.status)
        self.assertEqual("existing-head", result.baseline.expected_head_sha)
        self.assertNotEqual("advanced-intended-ref-head", result.baseline.expected_head_sha)

    def test_ambiguous_existing_work_stays_unresolved_without_intended_ref_fallback(self):
        first = observation(pr_number=1, branch="one", head_sha="one-head")
        second = observation(pr_number=2, branch="two", head_sha="two-head")
        result = resolve_execution_baseline(
            CHECKPOINT,
            request(),
            intended_ref(head_sha="intended-head"),
            discover_existing_work(CHECKPOINT, (second, first)),
        )

        self.assertEqual(ExecutionBaselineStatus.AMBIGUOUS_EXISTING_WORK, result.status)
        self.assertFalse(result.is_resolved)
        self.assertIsNone(result.baseline)

    def test_duplicate_active_existing_work_stays_unresolved(self):
        duplicate = observation()
        result = resolve_execution_baseline(
            CHECKPOINT,
            request(),
            intended_ref(),
            discover_existing_work(CHECKPOINT, (duplicate, duplicate)),
        )

        self.assertEqual(ExistingWorkMatch.AMBIGUOUS, discover_existing_work(CHECKPOINT, (duplicate, duplicate)).match)
        self.assertEqual(ExecutionBaselineStatus.AMBIGUOUS_EXISTING_WORK, result.status)
        self.assertIsNone(result.baseline)

    def test_intended_ref_mismatch_is_fail_safe_and_case_sensitive(self):
        result = resolve_execution_baseline(
            CHECKPOINT,
            request("origin/Main"),
            intended_ref("origin/main"),
            no_existing_work(),
        )

        self.assertEqual(ExecutionBaselineStatus.INPUT_MISMATCH, result.status)
        self.assertFalse(result.is_resolved)
        self.assertIsNone(result.baseline)

    def test_unique_existing_work_with_different_checkpoint_is_fail_safe_and_exact(self):
        mismatched = observation(checkpoint_id="core-08")
        supplied_discovery = ExistingWorkDiscovery(
            match=ExistingWorkMatch.UNIQUE,
            observations=(mismatched,),
        )
        result = resolve_execution_baseline(
            CHECKPOINT,
            request(),
            intended_ref(),
            supplied_discovery,
        )

        self.assertEqual(ExecutionBaselineStatus.INPUT_MISMATCH, result.status)
        self.assertFalse(result.is_resolved)
        self.assertIsNone(result.baseline)

    def test_head_strings_are_preserved_literally_without_normalization(self):
        result = resolve_execution_baseline(
            CHECKPOINT,
            request(),
            intended_ref(head_sha="AbC123/not-a-validated-sha"),
            no_existing_work(),
        )

        self.assertEqual("AbC123/not-a-validated-sha", result.baseline.expected_head_sha)

    def test_resolution_and_baseline_are_immutable(self):
        result = resolve_execution_baseline(CHECKPOINT, request(), intended_ref(), no_existing_work())

        self.assertIsInstance(result, ExecutionBaselineResolution)
        with self.assertRaises(FrozenInstanceError):
            result.status = ExecutionBaselineStatus.INPUT_MISMATCH
        with self.assertRaises(FrozenInstanceError):
            result.baseline.expected_head_sha = "other"


if __name__ == "__main__":
    unittest.main()
