from dataclasses import FrozenInstanceError
import unittest

from codex_autonomy_runner.existing_work import (
    ExistingWorkMatch,
    ExistingWorkObservation,
    discover_existing_work,
)


def observation(
    checkpoint_id="CORE-04", pr_number=10, branch="codex/core-04", head_sha="a" * 40, is_active=True
):
    return ExistingWorkObservation(
        checkpoint_id=checkpoint_id,
        pr_number=pr_number,
        branch=branch,
        head_sha=head_sha,
        is_active=is_active,
    )


class ExistingWorkTests(unittest.TestCase):
    def test_observation_preserves_exact_values_and_is_immutable(self):
        observed = observation(branch="feature/Exact Name", head_sha="AbC123")

        self.assertEqual("CORE-04", observed.checkpoint_id)
        self.assertEqual(10, observed.pr_number)
        self.assertEqual("feature/Exact Name", observed.branch)
        self.assertEqual("AbC123", observed.head_sha)
        self.assertTrue(observed.is_active)
        with self.assertRaises(FrozenInstanceError):
            observed.branch = "other"

    def test_empty_observations_produce_no_match(self):
        result = discover_existing_work("CORE-04", ())

        self.assertEqual(ExistingWorkMatch.NONE, result.match)
        self.assertEqual((), result.observations)
        self.assertIsNone(result.unique_observation)

    def test_other_checkpoint_and_inactive_observations_are_ignored(self):
        result = discover_existing_work(
            "CORE-04",
            (
                observation(checkpoint_id="CORE-03"),
                observation(is_active=False),
            ),
        )

        self.assertEqual(ExistingWorkMatch.NONE, result.match)
        self.assertEqual((), result.observations)

    def test_one_active_exact_match_is_unique_and_preserves_identity(self):
        observed = observation(branch="codex/exact", head_sha="b" * 40)

        result = discover_existing_work("CORE-04", (observed, observation("CORE-03")))

        self.assertEqual(ExistingWorkMatch.UNIQUE, result.match)
        self.assertEqual((observed,), result.observations)
        self.assertIs(observed, result.unique_observation)
        self.assertEqual(10, result.unique_observation.pr_number)
        self.assertEqual("codex/exact", result.unique_observation.branch)
        self.assertEqual("b" * 40, result.unique_observation.head_sha)

    def test_multiple_active_exact_matches_are_ambiguous_without_selection(self):
        first = observation(pr_number=3, branch="alpha", head_sha="c" * 40)
        second = observation(pr_number=4, branch="beta", head_sha="d" * 40)

        result = discover_existing_work("CORE-04", (second, first, observation("CORE-03")))

        self.assertEqual(ExistingWorkMatch.AMBIGUOUS, result.match)
        self.assertEqual((first, second), result.observations)
        self.assertIsNone(result.unique_observation)

    def test_match_order_is_deterministic_independent_of_input_order(self):
        first = observation(pr_number=2, branch="z", head_sha="f" * 40)
        second = observation(pr_number=1, branch="a", head_sha="e" * 40)

        forward = discover_existing_work("CORE-04", (first, second))
        reverse = discover_existing_work("CORE-04", (second, first))

        self.assertEqual(forward, reverse)
        self.assertEqual((second, first), forward.observations)

    def test_checkpoint_ids_are_compared_exactly_without_normalization(self):
        result = discover_existing_work("CORE-04", (observation(checkpoint_id="core-04"),))

        self.assertEqual(ExistingWorkMatch.NONE, result.match)

    def test_branch_and_head_values_are_not_normalized(self):
        observed = observation(branch="Feature/Case Sensitive", head_sha="AbCdEf")

        result = discover_existing_work("CORE-04", (observed,))

        self.assertEqual("Feature/Case Sensitive", result.unique_observation.branch)
        self.assertEqual("AbCdEf", result.unique_observation.head_sha)

    def test_duplicates_remain_ambiguous_and_are_not_deduplicated(self):
        duplicate = observation()

        result = discover_existing_work("CORE-04", (duplicate, duplicate))

        self.assertEqual(ExistingWorkMatch.AMBIGUOUS, result.match)
        self.assertEqual((duplicate, duplicate), result.observations)

    def test_discovery_is_immutable_and_exposes_tuple_observations(self):
        result = discover_existing_work("CORE-04", (observation(),))

        self.assertIsInstance(result.observations, tuple)
        with self.assertRaises(FrozenInstanceError):
            result.match = ExistingWorkMatch.NONE


if __name__ == "__main__":
    unittest.main()
