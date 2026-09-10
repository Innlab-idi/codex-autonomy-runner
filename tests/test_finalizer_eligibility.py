"""Tests for pure finalizer eligibility reconciliation."""

from dataclasses import FrozenInstanceError, replace
import unittest

from codex_autonomy_runner.existing_work import (
    ExistingWorkDiscovery,
    ExistingWorkMatch,
    ExistingWorkObservation,
    discover_existing_work,
)
from codex_autonomy_runner.finalizer_eligibility import (
    CurrentHeadRelationship,
    FinalizerEligibilityObservation,
    FinalizerEligibilityPath,
    FinalizerEligibilityViolation,
    FinalizerTarget,
    validate_finalizer_eligibility,
)
from codex_autonomy_runner.supervisor_decisions import (
    SupervisorDecisionDiscovery,
    SupervisorDecisionKind,
    SupervisorDecisionMatch,
    SupervisorDecisionObservation,
    discover_supervisor_decisions,
)


class FinalizerEligibilityTests(unittest.TestCase):
    def setUp(self):
        self.target = FinalizerTarget("repo-A", "FINALIZER-01", 17, "approved-head")
        self.existing = ExistingWorkObservation(
            "FINALIZER-01", 17, "codex/finalizer-01", "approved-head", True
        )
        self.approval = SupervisorDecisionObservation(
            "FINALIZER-01", 17, "approved-head", SupervisorDecisionKind.APPROVED, True
        )

    def observation(self, **changes):
        values = dict(
            repository_id="repo-A",
            checkpoint_id="FINALIZER-01",
            pr_number=17,
            checkpoint_gate="AI",
            checkpoint_state="AI_REVIEW",
            current_head_sha="approved-head",
            current_head_relationship=CurrentHeadRelationship.SUBSTANTIVE_HEAD,
            existing_work=discover_existing_work("FINALIZER-01", (self.existing,)),
            supervisor_decisions=discover_supervisor_decisions(
                "FINALIZER-01", 17, "approved-head", (self.approval,)
            ),
            has_later_invalidating_decision=False,
            has_later_substantive_change=False,
            checks_satisfied=True,
            is_mergeable=True,
            has_human_reserved_condition=False,
            is_non_destructive=True,
        )
        values.update(changes)
        return FinalizerEligibilityObservation(**values)

    def assert_violation(self, observation, violation):
        result = validate_finalizer_eligibility(self.target, observation)
        self.assertEqual(result.path, FinalizerEligibilityPath.INELIGIBLE)
        self.assertIn(violation, result.violations)
        self.assertIsNone(result.unit)

    def test_ai_review_approval_requires_closure(self):
        result = validate_finalizer_eligibility(self.target, self.observation())
        self.assertEqual(result.path, FinalizerEligibilityPath.CLOSURE_REQUIRED)
        self.assertTrue(result.is_eligible)
        self.assertTrue(result.closure_required)
        self.assertFalse(result.protected_merge_eligible)
        self.assertEqual(result.unit.current_head_sha, "approved-head")
        self.assertEqual(result.unit.branch, "codex/finalizer-01")

    def test_done_with_explicit_valid_closure_is_protected_merge_eligible(self):
        closed_work = replace(self.existing, head_sha="closure-head")
        result = validate_finalizer_eligibility(
            self.target,
            self.observation(
                checkpoint_state="DONE",
                current_head_sha="closure-head",
                current_head_relationship=CurrentHeadRelationship.VALID_ALLOWLISTED_CLOSURE,
                existing_work=discover_existing_work("FINALIZER-01", (closed_work,)),
            ),
        )
        self.assertEqual(result.path, FinalizerEligibilityPath.PROTECTED_MERGE)
        self.assertTrue(result.protected_merge_eligible)
        self.assertEqual(result.unit.repository_id, "repo-A")
        self.assertEqual(result.unit.checkpoint_id, "FINALIZER-01")
        self.assertEqual(result.unit.pr_number, 17)
        self.assertEqual(result.unit.branch, "codex/finalizer-01")
        self.assertEqual(result.unit.substantive_head_sha, "approved-head")
        self.assertEqual(result.unit.current_head_sha, "closure-head")

    def test_done_at_the_substantive_head_is_direct_protected_merge_eligible(self):
        result = validate_finalizer_eligibility(
            self.target,
            self.observation(
                checkpoint_state="DONE",
                current_head_relationship=CurrentHeadRelationship.SUBSTANTIVE_HEAD,
            ),
        )
        self.assertEqual(result.path, FinalizerEligibilityPath.PROTECTED_MERGE)
        self.assertTrue(result.protected_merge_eligible)
        self.assertEqual(result.unit.repository_id, "repo-A")
        self.assertEqual(result.unit.checkpoint_id, "FINALIZER-01")
        self.assertEqual(result.unit.pr_number, 17)
        self.assertEqual(result.unit.branch, "codex/finalizer-01")
        self.assertEqual(result.unit.substantive_head_sha, "approved-head")
        self.assertEqual(result.unit.current_head_sha, "approved-head")

    def test_identity_matching_is_literal_and_case_sensitive(self):
        self.assert_violation(
            self.observation(repository_id="repo-a"),
            FinalizerEligibilityViolation.REPOSITORY_IDENTITY_MISMATCH,
        )
        self.assert_violation(
            self.observation(checkpoint_id="finalizer-01"),
            FinalizerEligibilityViolation.CHECKPOINT_IDENTITY_MISMATCH,
        )
        self.assert_violation(
            self.observation(pr_number=18),
            FinalizerEligibilityViolation.PR_IDENTITY_MISMATCH,
        )

    def test_existing_work_requires_one_consistent_active_unit(self):
        self.assert_violation(
            self.observation(existing_work=ExistingWorkDiscovery(ExistingWorkMatch.NONE, ())),
            FinalizerEligibilityViolation.EXISTING_WORK_NONE,
        )
        second = replace(self.existing, branch="other")
        self.assert_violation(
            self.observation(
                existing_work=discover_existing_work("FINALIZER-01", (self.existing, second))
            ),
            FinalizerEligibilityViolation.EXISTING_WORK_AMBIGUOUS,
        )
        self.assert_violation(
            self.observation(
                existing_work=ExistingWorkDiscovery(
                    ExistingWorkMatch.UNIQUE, (replace(self.existing, pr_number=99),)
                )
            ),
            FinalizerEligibilityViolation.EXISTING_WORK_INCONSISTENT,
        )

    def test_existing_work_declared_cardinality_must_match_observations(self):
        for match in (ExistingWorkMatch.NONE, ExistingWorkMatch.AMBIGUOUS):
            with self.subTest(match=match):
                self.assert_violation(
                    self.observation(
                        existing_work=ExistingWorkDiscovery(match, (self.existing,))
                    ),
                    FinalizerEligibilityViolation.EXISTING_WORK_INCONSISTENT,
                )

    def test_gate_and_state_fail_closed(self):
        self.assert_violation(
            self.observation(checkpoint_gate="HUMAN"),
            FinalizerEligibilityViolation.GATE_NOT_AI,
        )
        self.assert_violation(
            self.observation(checkpoint_gate="ai"),
            FinalizerEligibilityViolation.GATE_NOT_AI,
        )
        self.assert_violation(
            self.observation(checkpoint_state="READY"),
            FinalizerEligibilityViolation.CHECKPOINT_STATE_NOT_FINALIZABLE,
        )

    def test_supervisor_discovery_requires_one_exact_current_approval(self):
        self.assert_violation(
            self.observation(
                supervisor_decisions=SupervisorDecisionDiscovery(
                    SupervisorDecisionMatch.NONE, ()
                )
            ),
            FinalizerEligibilityViolation.SUPERVISOR_DECISION_NONE,
        )
        rework = replace(self.approval, decision=SupervisorDecisionKind.AI_REWORK)
        self.assert_violation(
            self.observation(
                supervisor_decisions=discover_supervisor_decisions(
                    "FINALIZER-01", 17, "approved-head", (self.approval, rework)
                )
            ),
            FinalizerEligibilityViolation.SUPERVISOR_DECISION_AMBIGUOUS,
        )
        self.assert_violation(
            self.observation(
                supervisor_decisions=SupervisorDecisionDiscovery(
                    SupervisorDecisionMatch.UNIQUE,
                    (replace(self.approval, reviewed_head_sha="other-head"),),
                )
            ),
            FinalizerEligibilityViolation.SUPERVISOR_DECISION_INCONSISTENT,
        )

    def test_supervisor_declared_cardinality_must_match_observations(self):
        for match in (SupervisorDecisionMatch.NONE, SupervisorDecisionMatch.AMBIGUOUS):
            with self.subTest(match=match):
                self.assert_violation(
                    self.observation(
                        supervisor_decisions=SupervisorDecisionDiscovery(
                            match, (self.approval,)
                        )
                    ),
                    FinalizerEligibilityViolation.SUPERVISOR_DECISION_INCONSISTENT,
                )

    def test_non_approved_unique_decisions_are_refused(self):
        for decision in (
            SupervisorDecisionKind.AI_REWORK,
            SupervisorDecisionKind.HUMAN_REQUIRED,
        ):
            with self.subTest(decision=decision):
                self.assert_violation(
                    self.observation(
                        supervisor_decisions=discover_supervisor_decisions(
                            "FINALIZER-01",
                            17,
                            "approved-head",
                            (replace(self.approval, decision=decision),),
                        )
                    ),
                    FinalizerEligibilityViolation.SUPERVISOR_DECISION_NOT_APPROVED,
                )

    def test_invalidation_operational_and_human_preconditions_are_fail_closed(self):
        cases = (
            ("has_later_invalidating_decision", True, FinalizerEligibilityViolation.APPROVAL_INVALIDATED),
            ("has_later_substantive_change", True, FinalizerEligibilityViolation.LATER_SUBSTANTIVE_CHANGE),
            ("checks_satisfied", False, FinalizerEligibilityViolation.CHECKS_NOT_SATISFIED),
            ("is_mergeable", False, FinalizerEligibilityViolation.NOT_MERGEABLE),
            ("has_human_reserved_condition", True, FinalizerEligibilityViolation.HUMAN_RESERVED_CONDITION),
            ("is_non_destructive", False, FinalizerEligibilityViolation.NON_DESTRUCTIVE_PRECONDITION_UNSATISFIED),
        )
        for field, value, violation in cases:
            with self.subTest(field=field):
                self.assert_violation(self.observation(**{field: value}), violation)

    def test_current_head_relationship_is_explicit_and_state_bound(self):
        invalid_cases = (
            ("AI_REVIEW", CurrentHeadRelationship.VALID_ALLOWLISTED_CLOSURE, "closure-head"),
            ("AI_REVIEW", CurrentHeadRelationship.SUBSTANTIVE_HEAD, "other-head"),
            ("DONE", CurrentHeadRelationship.SUBSTANTIVE_HEAD, "other-head"),
            ("DONE", CurrentHeadRelationship.VALID_ALLOWLISTED_CLOSURE, "approved-head"),
        )
        for state, relationship, current_head in invalid_cases:
            with self.subTest(state=state, relationship=relationship, current_head=current_head):
                self.assert_violation(
                    self.observation(
                        checkpoint_state=state,
                        current_head_sha=current_head,
                        current_head_relationship=relationship,
                        existing_work=discover_existing_work(
                            "FINALIZER-01",
                            (replace(self.existing, head_sha=current_head),),
                        ),
                    ),
                    FinalizerEligibilityViolation.CURRENT_HEAD_RELATIONSHIP_INCOMPATIBLE,
                )

    def test_multiple_violations_follow_enum_declaration_order(self):
        result = validate_finalizer_eligibility(
            self.target,
            self.observation(
                repository_id="wrong",
                checkpoint_gate="ai",
                has_later_substantive_change=True,
                checks_satisfied=False,
            ),
        )
        self.assertEqual(
            result.violations,
            (
                FinalizerEligibilityViolation.REPOSITORY_IDENTITY_MISMATCH,
                FinalizerEligibilityViolation.GATE_NOT_AI,
                FinalizerEligibilityViolation.LATER_SUBSTANTIVE_CHANGE,
                FinalizerEligibilityViolation.CHECKS_NOT_SATISFIED,
            ),
        )

    def test_types_and_returned_collections_are_immutable(self):
        observation = self.observation()
        result = validate_finalizer_eligibility(self.target, observation)
        with self.assertRaises(FrozenInstanceError):
            self.target.repository_id = "other"
        with self.assertRaises(FrozenInstanceError):
            observation.checkpoint_gate = "HUMAN"
        with self.assertRaises(FrozenInstanceError):
            result.path = FinalizerEligibilityPath.INELIGIBLE
        self.assertIsInstance(result.violations, tuple)


if __name__ == "__main__":
    unittest.main()
