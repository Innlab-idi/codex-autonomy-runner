from dataclasses import FrozenInstanceError
import unittest

from codex_autonomy_runner.supervisor_decisions import (
    SupervisorDecisionKind,
    SupervisorDecisionMatch,
    SupervisorDecisionObservation,
    discover_supervisor_decisions,
)


def observation(
    checkpoint_id="CORE-05",
    pr_number=42,
    reviewed_head_sha="a" * 40,
    decision=SupervisorDecisionKind.APPROVED,
    is_current=True,
):
    return SupervisorDecisionObservation(
        checkpoint_id=checkpoint_id,
        pr_number=pr_number,
        reviewed_head_sha=reviewed_head_sha,
        decision=decision,
        is_current=is_current,
    )


class SupervisorDecisionTests(unittest.TestCase):
    def discover(self, observations):
        return discover_supervisor_decisions("CORE-05", 42, "a" * 40, observations)

    def test_observation_preserves_values_and_is_immutable(self):
        observed = observation(reviewed_head_sha="AbC123")

        self.assertEqual("CORE-05", observed.checkpoint_id)
        self.assertEqual(42, observed.pr_number)
        self.assertEqual("AbC123", observed.reviewed_head_sha)
        self.assertEqual(SupervisorDecisionKind.APPROVED, observed.decision)
        self.assertTrue(observed.is_current)
        with self.assertRaises(FrozenInstanceError):
            observed.decision = SupervisorDecisionKind.AI_REWORK

    def test_decision_kind_has_exactly_three_semantic_classes(self):
        self.assertEqual(
            {
                SupervisorDecisionKind.APPROVED,
                SupervisorDecisionKind.AI_REWORK,
                SupervisorDecisionKind.HUMAN_REQUIRED,
            },
            set(SupervisorDecisionKind),
        )

    def test_empty_observations_produce_none(self):
        result = self.discover(())

        self.assertEqual(SupervisorDecisionMatch.NONE, result.match)
        self.assertEqual((), result.observations)
        self.assertIsNone(result.unique_observation)
        self.assertIsNone(result.unique_decision)

    def test_other_checkpoint_pr_head_and_non_current_observations_are_ignored(self):
        result = self.discover(
            (
                observation(checkpoint_id="CORE-04"),
                observation(pr_number=43),
                observation(reviewed_head_sha="b" * 40),
                observation(is_current=False),
            )
        )

        self.assertEqual(SupervisorDecisionMatch.NONE, result.match)
        self.assertEqual((), result.observations)

    def test_each_decision_kind_can_be_a_unique_current_match(self):
        for decision in SupervisorDecisionKind:
            with self.subTest(decision=decision):
                observed = observation(decision=decision)
                result = self.discover((observed,))

                self.assertEqual(SupervisorDecisionMatch.UNIQUE, result.match)
                self.assertIs(observed, result.unique_observation)
                self.assertEqual(decision, result.unique_decision)
                self.assertEqual("CORE-05", result.unique_observation.checkpoint_id)
                self.assertEqual(42, result.unique_observation.pr_number)
                self.assertEqual("a" * 40, result.unique_observation.reviewed_head_sha)

    def test_approved_and_ai_rework_remain_ambiguous_without_precedence(self):
        approved = observation(decision=SupervisorDecisionKind.APPROVED)
        rework = observation(decision=SupervisorDecisionKind.AI_REWORK)

        result = self.discover((rework, approved))

        self.assertEqual(SupervisorDecisionMatch.AMBIGUOUS, result.match)
        self.assertEqual((rework, approved), result.observations)
        self.assertIsNone(result.unique_observation)
        self.assertIsNone(result.unique_decision)

    def test_approved_and_human_required_remain_ambiguous_without_precedence(self):
        approved = observation(decision=SupervisorDecisionKind.APPROVED)
        human_required = observation(decision=SupervisorDecisionKind.HUMAN_REQUIRED)

        result = self.discover((approved, human_required))

        self.assertEqual(SupervisorDecisionMatch.AMBIGUOUS, result.match)
        self.assertIsNone(result.unique_decision)

    def test_duplicates_remain_ambiguous_and_are_not_deduplicated(self):
        duplicate = observation()

        result = self.discover((duplicate, duplicate))

        self.assertEqual(SupervisorDecisionMatch.AMBIGUOUS, result.match)
        self.assertEqual((duplicate, duplicate), result.observations)

    def test_ambiguous_order_is_deterministic_independent_of_input_order(self):
        approved = observation(decision=SupervisorDecisionKind.APPROVED)
        rework = observation(decision=SupervisorDecisionKind.AI_REWORK)
        human_required = observation(decision=SupervisorDecisionKind.HUMAN_REQUIRED)

        forward = self.discover((approved, rework, human_required))
        reverse = self.discover((human_required, rework, approved))

        self.assertEqual(forward, reverse)
        self.assertEqual((rework, approved, human_required), forward.observations)

    def test_checkpoint_and_head_matching_are_exact_without_normalization(self):
        checkpoint_result = self.discover((observation(checkpoint_id="core-05"),))
        head_result = self.discover((observation(reviewed_head_sha="A" * 40),))

        self.assertEqual(SupervisorDecisionMatch.NONE, checkpoint_result.match)
        self.assertEqual(SupervisorDecisionMatch.NONE, head_result.match)

    def test_discovery_and_observation_collection_are_immutable(self):
        result = self.discover((observation(),))

        self.assertIsInstance(result.observations, tuple)
        with self.assertRaises(FrozenInstanceError):
            result.match = SupervisorDecisionMatch.NONE


if __name__ == "__main__":
    unittest.main()
