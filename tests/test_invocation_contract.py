from dataclasses import FrozenInstanceError, fields
from pathlib import Path
import unittest

from codex_autonomy_runner.invocation_contract import (
    InvocationOutcome,
    InvocationRequest,
    InvocationResult,
)


class InvocationContractTests(unittest.TestCase):
    def test_request_preserves_selected_repository_and_intended_ref(self):
        repository = Path("C:/repositories/consumer")
        request = InvocationRequest(repository=repository, intended_ref="main")

        self.assertEqual(repository, request.repository)
        self.assertEqual("main", request.intended_ref)

    def test_request_is_immutable(self):
        request = InvocationRequest(repository=Path("repository"), intended_ref="main")

        with self.assertRaises(FrozenInstanceError):
            request.intended_ref = "other"

    def test_outcome_enum_has_all_required_distinct_outcomes(self):
        self.assertEqual(
            {
                InvocationOutcome.COMPLETED_AUTHORIZED_TRANSITION,
                InvocationOutcome.NO_OP,
                InvocationOutcome.BLOCKED,
                InvocationOutcome.RUNTIME_EXECUTION_FAILURE,
                InvocationOutcome.INTERRUPTED_OR_UNTRUSTWORTHY,
            },
            set(InvocationOutcome),
        )

    def test_each_outcome_creates_a_structured_result(self):
        for outcome in InvocationOutcome:
            with self.subTest(outcome=outcome):
                self.assertEqual(outcome, InvocationResult(outcome=outcome).outcome)

    def test_result_is_immutable(self):
        result = InvocationResult(InvocationOutcome.NO_OP)

        with self.assertRaises(FrozenInstanceError):
            result.outcome = InvocationOutcome.COMPLETED_AUTHORIZED_TRANSITION

    def test_no_op_is_structurally_distinct_from_completed_transition(self):
        self.assertNotEqual(
            InvocationOutcome.NO_OP,
            InvocationOutcome.COMPLETED_AUTHORIZED_TRANSITION,
        )

    def test_blocked_is_structurally_distinct_from_runtime_failure(self):
        self.assertNotEqual(
            InvocationOutcome.BLOCKED,
            InvocationOutcome.RUNTIME_EXECUTION_FAILURE,
        )

    def test_untrustworthy_execution_is_distinct_from_reliable_outcomes(self):
        untrustworthy = InvocationOutcome.INTERRUPTED_OR_UNTRUSTWORTHY

        self.assertNotEqual(untrustworthy, InvocationOutcome.COMPLETED_AUTHORIZED_TRANSITION)
        self.assertNotEqual(untrustworthy, InvocationOutcome.NO_OP)
        self.assertNotEqual(untrustworthy, InvocationOutcome.BLOCKED)
        self.assertNotEqual(untrustworthy, InvocationOutcome.RUNTIME_EXECUTION_FAILURE)

    def test_result_outcome_requires_no_description_field(self):
        self.assertEqual(("outcome",), tuple(field.name for field in fields(InvocationResult)))


if __name__ == "__main__":
    unittest.main()
