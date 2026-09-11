"""Deterministic tests for FINALIZER-04's exact-head merge boundary."""

from dataclasses import replace
import unittest

from codex_autonomy_runner.finalizer_eligibility import EligibleFinalizerUnit, FinalizerEligibility, FinalizerEligibilityPath, FinalizerEligibilityViolation
from codex_autonomy_runner.finalizer_eligibility import CurrentHeadRelationship, FinalizerEligibilityObservation, FinalizerTarget, validate_finalizer_eligibility
from codex_autonomy_runner.existing_work import ExistingWorkObservation, discover_existing_work
from codex_autonomy_runner.supervisor_decisions import SupervisorDecisionKind, SupervisorDecisionObservation, discover_supervisor_decisions
from codex_autonomy_runner.finalizer_merge import FinalizerMergeStatus, FinalizerMergeViolation, ProtectedMergeTransportResult, merge_after_fresh_revalidation


class RecordingTransport:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.requests = result or ProtectedMergeTransportResult(True, "merge"), error, []
    def merge_protected(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return self.result


class FinalizerMergeTests(unittest.TestCase):
    def eligibility(self, current="substantive"):
        unit = EligibleFinalizerUnit("repo", "FINALIZER-04", 44, "branch", "substantive", current)
        return FinalizerEligibility(FinalizerEligibilityPath.PROTECTED_MERGE, (), unit)

    def fresh_eligibility(self, current="substantive", relationship=CurrentHeadRelationship.SUBSTANTIVE_HEAD, state="DONE", **changes):
        target = FinalizerTarget("repo", "FINALIZER-04", 44, "substantive")
        values = dict(
            repository_id="repo", checkpoint_id="FINALIZER-04", pr_number=44,
            checkpoint_gate="AI", checkpoint_state=state, current_head_sha=current,
            current_head_relationship=relationship,
            existing_work=discover_existing_work("FINALIZER-04", (ExistingWorkObservation("FINALIZER-04", 44, "branch", current, True),)),
            supervisor_decisions=discover_supervisor_decisions("FINALIZER-04", 44, "substantive", (SupervisorDecisionObservation("FINALIZER-04", 44, "substantive", SupervisorDecisionKind.APPROVED, True),)),
            has_later_invalidating_decision=False, has_later_substantive_change=False,
            checks_satisfied=True, is_mergeable=True, has_human_reserved_condition=False,
            is_non_destructive=True,
        )
        values.update(changes)
        return validate_finalizer_eligibility(target, FinalizerEligibilityObservation(**values))

    def test_direct_merge_happy_path_preserves_identity_and_sha(self):
        transport = RecordingTransport(ProtectedMergeTransportResult(True, "merge-direct"))
        eligibility = self.fresh_eligibility()
        result = merge_after_fresh_revalidation(eligibility, transport)
        self.assertEqual(FinalizerMergeStatus.COMPLETED, result.status)
        self.assertEqual("merge-direct", result.merge_commit_sha)
        self.assertEqual(1, len(transport.requests))
        self.assertEqual(("repo", 44, eligibility.unit.current_head_sha), (transport.requests[0].repository_id, transport.requests[0].pr_number, transport.requests[0].expected_head_sha))

    def test_post_closure_happy_path_uses_closure_head_not_substantive(self):
        eligibility = self.fresh_eligibility("closure", CurrentHeadRelationship.VALID_ALLOWLISTED_CLOSURE)
        self.assertEqual(FinalizerEligibilityPath.PROTECTED_MERGE, eligibility.path)
        transport = RecordingTransport(ProtectedMergeTransportResult(True, "merge-closure"))
        result = merge_after_fresh_revalidation(eligibility, transport)
        self.assertEqual(FinalizerMergeStatus.COMPLETED, result.status)
        self.assertEqual(1, len(transport.requests))
        self.assertEqual("closure", transport.requests[0].expected_head_sha)
        self.assertNotEqual("substantive", transport.requests[0].expected_head_sha)
        self.assertEqual(("repo", 44), (transport.requests[0].repository_id, transport.requests[0].pr_number))

    def test_closure_required_and_fresh_invalidations_never_call_transport(self):
        transport = RecordingTransport()
        closure_required = self.fresh_eligibility(state="AI_REVIEW")
        self.assertEqual(FinalizerEligibilityPath.CLOSURE_REQUIRED, closure_required.path)
        self.assertEqual(FinalizerMergeStatus.REJECTED, merge_after_fresh_revalidation(closure_required, transport).status)
        for field, value in (("has_later_substantive_change", True), ("has_later_invalidating_decision", True), ("checks_satisfied", False), ("is_mergeable", False), ("has_human_reserved_condition", True)):
            with self.subTest(field=field):
                eligibility = self.fresh_eligibility(**{field: value})
                self.assertEqual(FinalizerEligibilityPath.INELIGIBLE, eligibility.path)
                self.assertEqual(FinalizerMergeStatus.REJECTED, merge_after_fresh_revalidation(eligibility, transport).status)
        self.assertEqual([], transport.requests)

    def test_non_merge_paths_and_incoherent_eligibility_never_call_transport(self):
        transport = RecordingTransport()
        for eligibility in (
            replace(self.eligibility(), path=FinalizerEligibilityPath.CLOSURE_REQUIRED),
            FinalizerEligibility(FinalizerEligibilityPath.INELIGIBLE, (), None),
            FinalizerEligibility(FinalizerEligibilityPath.PROTECTED_MERGE, (FinalizerEligibilityViolation.NOT_MERGEABLE,), self.eligibility().unit),
            FinalizerEligibility(FinalizerEligibilityPath.PROTECTED_MERGE, (), None),
            FinalizerEligibility(FinalizerEligibilityPath.PROTECTED_MERGE, (), replace(self.eligibility().unit, repository_id="")),
        ):
            self.assertEqual(FinalizerMergeStatus.REJECTED, merge_after_fresh_revalidation(eligibility, transport).status)
        self.assertEqual([], transport.requests)

    def test_expected_head_movement_is_a_structured_refusal(self):
        transport = RecordingTransport(ProtectedMergeTransportResult(False, expected_head_satisfied=False))
        result = merge_after_fresh_revalidation(self.eligibility("closure"), transport)
        self.assertEqual(FinalizerMergeStatus.REJECTED, result.status)
        self.assertEqual((FinalizerMergeViolation.TRANSPORT_REFUSED,), result.violations)
        self.assertIsNone(result.merge_commit_sha)
        self.assertEqual(1, len(transport.requests))
        self.assertEqual("closure", transport.requests[0].expected_head_sha)
        self.assertNotEqual(FinalizerMergeStatus.OPERATIONAL_FAILURE, result.status)

    def test_malformed_transport_success_is_rejected_exactly(self):
        for outcome in (
            ProtectedMergeTransportResult(True, None),
            ProtectedMergeTransportResult(True, "merge", False),
        ):
            with self.subTest(outcome=outcome):
                transport = RecordingTransport(outcome)
                result = merge_after_fresh_revalidation(self.eligibility("closure"), transport)
                self.assertEqual(FinalizerMergeStatus.REJECTED, result.status)
                self.assertEqual((FinalizerMergeViolation.TRANSPORT_SUCCESS_INCONSISTENT,), result.violations)
                self.assertIsNone(result.merge_commit_sha)
                self.assertEqual(1, len(transport.requests))
                self.assertNotEqual(FinalizerMergeStatus.COMPLETED, result.status)
                self.assertNotEqual(FinalizerMergeStatus.OPERATIONAL_FAILURE, result.status)

    def test_refusal_and_operational_failure_have_exact_statuses_without_retry(self):
        refusal = RecordingTransport(ProtectedMergeTransportResult(False))
        result = merge_after_fresh_revalidation(self.eligibility("closure"), refusal)
        self.assertEqual(FinalizerMergeStatus.REJECTED, result.status)
        self.assertEqual((FinalizerMergeViolation.TRANSPORT_REFUSED,), result.violations)
        self.assertIsNone(result.merge_commit_sha)
        self.assertEqual(1, len(refusal.requests))
        failed = RecordingTransport(error=OSError("transport"))
        result = merge_after_fresh_revalidation(self.eligibility("closure"), failed)
        self.assertEqual(FinalizerMergeStatus.OPERATIONAL_FAILURE, result.status)
        self.assertIsNone(result.merge_commit_sha)
        self.assertEqual(1, len(failed.requests))


if __name__ == "__main__":
    unittest.main()
