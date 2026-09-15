"""Controlled RUNTIME-05 composition tests; no live durable service."""

from dataclasses import fields, replace
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from codex_autonomy_runner.execution_baseline import IntendedRefObservation
from codex_autonomy_runner.existing_work import discover_existing_work
from codex_autonomy_runner.invocation_contract import InvocationOutcome, InvocationRequest, InvocationResult
from codex_autonomy_runner.native_process import run_native_process
from codex_autonomy_runner.runtime_durable_state import FinalizerPrepassResult, FinalizerPrepassStatus
from codex_autonomy_runner.runtime_publication import RuntimePublicationRequest, RuntimePublicationStatus
from codex_autonomy_runner.runtime_wake import (
    AuthorizedTransition, CoordinatorDisposition, CoordinatorResponse,
    DurableWakeState, RecoveryEvidence, WakeHostServices, WakeStage,
    run_repository_wake,
)
from codex_autonomy_runner.runtime_worker import CheckDeclaration, CheckKind, RuntimeWorkerStatus


class Refresher:
    def __init__(self, states, events):
        self.states = list(states)
        self.events = events

    def refresh(self, context):
        self.events.append("refresh")
        return self.states.pop(0)


class Coordinator:
    def __init__(self, response, events):
        self.response = response
        self.events = events

    def coordinate(self, observation):
        self.events.append("coordinator")
        return self.response


class Executor:
    def execute(self, context):
        raise AssertionError("worker is patched in composition tests")


class CheckExecutor:
    def execute(self, context):
        raise AssertionError("checks are patched in composition tests")


class Transport:
    def observe_remote_branch(self, *args): pass
    def push_non_force(self, *args): pass
    def create_pull_request(self, *args): pass
    def observe_pull_request(self, *args): pass


class RuntimeWakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        self.git("init", "-b", "base")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                 "commit", "--allow-empty", "-m", "fixture")
        self.head = self.git("rev-parse", "HEAD")
        self.request = InvocationRequest(self.repo, "base")
        self.events = []
        self.empty = DurableWakeState(object())
        self.host = WakeHostServices(Executor(), publication_transport=Transport())

    def git(self, *args):
        result = run_native_process(("git", *args), cwd=self.repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def transition(self, *, checks=(), paths=("allowed.txt",), branch="chosen",
                   request_paths=None, required_checks=(), repository=None,
                   publication_branch=None):
        request_paths = paths if request_paths is None else request_paths
        repository = self.repo if repository is None else repository
        publication_branch = branch if publication_branch is None else publication_branch
        return AuthorizedTransition(
            "CHECK", IntendedRefObservation("base", self.head), discover_existing_work("CHECK", ()),
            paths, checks, branch, "attempt-1", publication_request=RuntimePublicationRequest(
                repository, "repo-id", publication_branch, request_paths, required_checks, "private message",
                "base", "title", "private body",
            ), instructions="private instructions",
        )

    def invoke(self, states=None, response=None, host=None, **kwargs):
        return run_repository_wake(
            self.request, Refresher(states or [self.empty], self.events),
            Coordinator(response or CoordinatorResponse(CoordinatorDisposition.NO_OP), self.events),
            self.host if host is None else host,
            **kwargs,
        )

    def prepass(self, status, outcome, refresh=False):
        return FinalizerPrepassResult(status, InvocationResult(outcome), refresh, None)

    def worker(self, status, outcome=None):
        return SimpleNamespace(status=status, invocation_result=(
            InvocationResult(outcome) if outcome else None
        ))

    def publication(self, status, outcome):
        return SimpleNamespace(status=status, invocation_result=InvocationResult(outcome))

    def test_no_op_orders_refresh_finalizer_coordinator_and_releases_lock(self):
        with patch("codex_autonomy_runner.runtime_wake.run_finalizer_prepass", side_effect=lambda *a, **k: self.events.append("finalizer") or self.prepass(FinalizerPrepassStatus.NO_FINALIZATION, InvocationOutcome.NO_OP)) as finalizer, \
             patch("codex_autonomy_runner.runtime_wake.run_runtime_worker") as worker, \
             patch("codex_autonomy_runner.runtime_wake.publish_validated_worker_work") as publication:
            report = self.invoke()
        self.assertIs(report.result.outcome, InvocationOutcome.NO_OP)
        self.assertEqual(self.events, ["refresh", "coordinator"])
        self.assertEqual(report.evidence.stages, (WakeStage.DURABLE_REFRESH, WakeStage.FINALIZER_PREPASS, WakeStage.COORDINATOR))
        self.assertEqual(report.evidence.worker_attempts, 0)
        self.assertFalse(report.evidence.publication_attempted)
        self.assertTrue(report.invocation.evidence.lock_released)
        finalizer.assert_not_called()  # no directed finalizer context means no artificial prepass
        worker.assert_not_called(); publication.assert_not_called()

    def test_successful_transition_calls_worker_and_publication_once(self):
        response = CoordinatorResponse(CoordinatorDisposition.TRANSITION, self.transition())
        with patch("codex_autonomy_runner.runtime_wake.run_runtime_worker", return_value=self.worker(RuntimeWorkerStatus.VALIDATED)) as worker, \
             patch("codex_autonomy_runner.runtime_wake.publish_validated_worker_work", return_value=self.publication(RuntimePublicationStatus.VERIFIED, InvocationOutcome.COMPLETED_AUTHORIZED_TRANSITION)) as publication:
            report = self.invoke(response=response)
        self.assertIs(report.result.outcome, InvocationOutcome.COMPLETED_AUTHORIZED_TRANSITION)
        worker.assert_called_once(); publication.assert_called_once()
        self.assertEqual(report.evidence.worker_attempts, 1)
        self.assertTrue(report.evidence.publication_attempted)
        self.assertTrue(report.invocation.evidence.lock_released)

    def test_finalizer_mutation_requires_second_refresh_before_coordinator(self):
        directed = DurableWakeState(object(), finalizer_context=object(), durable_transport=object())
        response = CoordinatorResponse(CoordinatorDisposition.NO_OP)
        def finalizer(*args, **kwargs):
            self.events.append("finalizer")
            return self.prepass(FinalizerPrepassStatus.MERGED, InvocationOutcome.COMPLETED_AUTHORIZED_TRANSITION, True)
        with patch("codex_autonomy_runner.runtime_wake.run_finalizer_prepass", side_effect=finalizer) as finalizer_call, \
             patch("codex_autonomy_runner.runtime_wake.run_runtime_worker") as worker, \
             patch("codex_autonomy_runner.runtime_wake.publish_validated_worker_work") as publication:
            report = self.invoke([directed, self.empty], response)
        self.assertIs(report.result.outcome, InvocationOutcome.COMPLETED_AUTHORIZED_TRANSITION)
        self.assertEqual(self.events, ["refresh", "finalizer", "refresh", "coordinator"])
        self.assertEqual(report.evidence.refresh_count, 2)
        self.assertEqual(report.evidence.stages, (WakeStage.DURABLE_REFRESH, WakeStage.FINALIZER_PREPASS, WakeStage.POST_FINALIZER_REFRESH, WakeStage.COORDINATOR))
        finalizer_call.assert_called_once(); worker.assert_not_called(); publication.assert_not_called()

    def test_closure_finalization_then_no_op_returns_completed_transition(self):
        directed = DurableWakeState(object(), finalizer_context=object(), durable_transport=object())
        prepass = self.prepass(
            FinalizerPrepassStatus.CLOSURE_PUBLISHED,
            InvocationOutcome.COMPLETED_AUTHORIZED_TRANSITION,
            True,
        )
        with patch("codex_autonomy_runner.runtime_wake.run_finalizer_prepass", return_value=prepass) as finalizer, \
             patch("codex_autonomy_runner.runtime_wake.run_runtime_worker") as worker, \
             patch("codex_autonomy_runner.runtime_wake.publish_validated_worker_work") as publication:
            report = self.invoke([directed, self.empty], CoordinatorResponse(CoordinatorDisposition.NO_OP))
        self.assertIs(report.result.outcome, InvocationOutcome.COMPLETED_AUTHORIZED_TRANSITION)
        self.assertEqual(report.evidence.refresh_count, 2)
        self.assertTrue(report.invocation.evidence.lock_released)
        finalizer.assert_called_once(); worker.assert_not_called(); publication.assert_not_called()

    def test_directed_no_finalization_then_no_op_remains_no_op(self):
        directed = DurableWakeState(object(), finalizer_context=object(), durable_transport=object())
        with patch("codex_autonomy_runner.runtime_wake.run_finalizer_prepass", return_value=self.prepass(FinalizerPrepassStatus.NO_FINALIZATION, InvocationOutcome.NO_OP)) as finalizer:
            report = self.invoke([directed], CoordinatorResponse(CoordinatorDisposition.NO_OP))
        self.assertIs(report.result.outcome, InvocationOutcome.NO_OP)
        finalizer.assert_called_once()

    def test_second_refresh_failure_stops_before_coordinator_worker_and_publication(self):
        directed = DurableWakeState(object(), finalizer_context=object(), durable_transport=object())
        with patch("codex_autonomy_runner.runtime_wake.run_finalizer_prepass", return_value=self.prepass(FinalizerPrepassStatus.CLOSURE_PUBLISHED, InvocationOutcome.COMPLETED_AUTHORIZED_TRANSITION, True)), \
             patch("codex_autonomy_runner.runtime_wake.run_runtime_worker") as worker, \
             patch("codex_autonomy_runner.runtime_wake.publish_validated_worker_work") as publication:
            report = self.invoke([directed], CoordinatorResponse(CoordinatorDisposition.NO_OP))
        self.assertIs(report.result.outcome, InvocationOutcome.INTERRUPTED_OR_UNTRUSTWORTHY)
        worker.assert_not_called(); publication.assert_not_called()
        self.assertTrue(report.invocation.evidence.lock_released)

    def test_finalizer_mutation_later_worker_or_publication_result_has_precedence(self):
        directed = DurableWakeState(object(), finalizer_context=object(), durable_transport=object())
        response = CoordinatorResponse(CoordinatorDisposition.TRANSITION, self.transition())
        prepass = self.prepass(
            FinalizerPrepassStatus.MERGED,
            InvocationOutcome.COMPLETED_AUTHORIZED_TRANSITION,
            True,
        )
        cases = (
            ("worker-failure", self.worker(RuntimeWorkerStatus.PREPARATION_FAILED, InvocationOutcome.RUNTIME_EXECUTION_FAILURE), None, InvocationOutcome.RUNTIME_EXECUTION_FAILURE),
            ("publication-uncertain", self.worker(RuntimeWorkerStatus.VALIDATED), self.publication(RuntimePublicationStatus.REMOTE_UNTRUSTWORTHY, InvocationOutcome.INTERRUPTED_OR_UNTRUSTWORTHY), InvocationOutcome.INTERRUPTED_OR_UNTRUSTWORTHY),
        )
        for name, worker_result, publication_result, outcome in cases:
            with self.subTest(name=name), \
                 patch("codex_autonomy_runner.runtime_wake.run_finalizer_prepass", return_value=prepass) as finalizer, \
                 patch("codex_autonomy_runner.runtime_wake.run_runtime_worker", return_value=worker_result) as worker, \
                 patch("codex_autonomy_runner.runtime_wake.publish_validated_worker_work", return_value=publication_result) as publication:
                report = self.invoke([directed, self.empty], response)
            self.assertIs(report.result.outcome, outcome)
            finalizer.assert_called_once(); worker.assert_called_once()
            if publication_result is None:
                publication.assert_not_called()
            else:
                publication.assert_called_once()

    def test_finalizer_runtime_failure_and_untrustworthy_are_terminal(self):
        directed = DurableWakeState(object(), finalizer_context=object(), durable_transport=object())
        for status, outcome in ((FinalizerPrepassStatus.RUNTIME_FAILURE, InvocationOutcome.RUNTIME_EXECUTION_FAILURE), (FinalizerPrepassStatus.INTERRUPTED_OR_UNTRUSTWORTHY, InvocationOutcome.INTERRUPTED_OR_UNTRUSTWORTHY)):
            with self.subTest(status=status), patch("codex_autonomy_runner.runtime_wake.run_finalizer_prepass", return_value=self.prepass(status, outcome)) as finalizer, patch("codex_autonomy_runner.runtime_wake.run_runtime_worker") as worker:
                report = self.invoke([directed], CoordinatorResponse(CoordinatorDisposition.NO_OP))
            self.assertIs(report.result.outcome, outcome)
            finalizer.assert_called_once(); worker.assert_not_called()

    def test_worker_failures_never_publish(self):
        response = CoordinatorResponse(CoordinatorDisposition.TRANSITION, self.transition())
        for status, outcome in ((RuntimeWorkerStatus.PREPARATION_FAILED, InvocationOutcome.RUNTIME_EXECUTION_FAILURE), (RuntimeWorkerStatus.WORKER_UNTRUSTWORTHY, InvocationOutcome.INTERRUPTED_OR_UNTRUSTWORTHY), (RuntimeWorkerStatus.BOUNDARY_INVALID, InvocationOutcome.INTERRUPTED_OR_UNTRUSTWORTHY), (RuntimeWorkerStatus.FOCUSED_UNSATISFIED, InvocationOutcome.RUNTIME_EXECUTION_FAILURE), (RuntimeWorkerStatus.PUBLICATION_UNSATISFIED, InvocationOutcome.RUNTIME_EXECUTION_FAILURE), (RuntimeWorkerStatus.CHECK_RUNTIME_FAILURE, InvocationOutcome.RUNTIME_EXECUTION_FAILURE), (RuntimeWorkerStatus.CHECK_UNTRUSTWORTHY, InvocationOutcome.INTERRUPTED_OR_UNTRUSTWORTHY)):
            with self.subTest(status=status), patch("codex_autonomy_runner.runtime_wake.run_runtime_worker", return_value=self.worker(status, outcome)) as worker, patch("codex_autonomy_runner.runtime_wake.publish_validated_worker_work") as publication:
                report = self.invoke(response=response)
            self.assertIs(report.result.outcome, outcome)
            worker.assert_called_once(); publication.assert_not_called()

    def test_publication_refusal_and_uncertainty_preserve_mapping_without_retry(self):
        response = CoordinatorResponse(CoordinatorDisposition.TRANSITION, self.transition())
        for status, outcome in ((RuntimePublicationStatus.REFUSED, InvocationOutcome.RUNTIME_EXECUTION_FAILURE), (RuntimePublicationStatus.REMOTE_UNTRUSTWORTHY, InvocationOutcome.INTERRUPTED_OR_UNTRUSTWORTHY)):
            with self.subTest(status=status), patch("codex_autonomy_runner.runtime_wake.run_runtime_worker", return_value=self.worker(RuntimeWorkerStatus.VALIDATED)) as worker, patch("codex_autonomy_runner.runtime_wake.publish_validated_worker_work", return_value=self.publication(status, outcome)) as publication:
                report = self.invoke(response=response)
            self.assertIs(report.result.outcome, outcome)
            worker.assert_called_once(); publication.assert_called_once()
            self.assertEqual(report.evidence.worker_attempts, 1)

    def test_invalid_or_multiple_like_coordinator_response_never_calls_worker(self):
        invalid = CoordinatorResponse(CoordinatorDisposition.TRANSITION, None)
        with patch("codex_autonomy_runner.runtime_wake.run_runtime_worker") as worker:
            report = self.invoke(response=invalid)
        self.assertIs(report.result.outcome, InvocationOutcome.RUNTIME_EXECUTION_FAILURE)
        worker.assert_not_called()

    def test_blocked_is_preserved_only_when_supplied_by_coordinator(self):
        with patch("codex_autonomy_runner.runtime_wake.run_runtime_worker") as worker, \
             patch("codex_autonomy_runner.runtime_wake.publish_validated_worker_work") as publication:
            report = self.invoke(response=CoordinatorResponse(CoordinatorDisposition.BLOCKED))
        self.assertIs(report.result.outcome, InvocationOutcome.BLOCKED)
        worker.assert_not_called(); publication.assert_not_called()

    def test_authorized_transition_is_data_only(self):
        names = {item.name for item in fields(AuthorizedTransition)}
        self.assertNotIn("worker_executor", names)
        self.assertNotIn("check_executor", names)
        self.assertNotIn("publication_transport", names)
        self.assertNotIn("execution_profile", names)

    def test_invalid_host_and_transition_coherence_fail_before_worker(self):
        check = CheckDeclaration("publication", CheckKind.PUBLICATION, ("check",))
        complete = WakeHostServices(Executor(), CheckExecutor(), Transport())
        other = self.repo.parent / "other"
        cases = (
            ("missing-worker", self.transition(), WakeHostServices(None, None, Transport())),
            ("missing-check", self.transition(checks=(check,), required_checks=("publication",)), self.host),
            ("incomplete-transport", self.transition(), WakeHostServices(Executor(), None, object())),
            ("path-mismatch", self.transition(request_paths=("other.txt",)), complete),
            ("repository-mismatch", self.transition(repository=other), complete),
            ("required-check-undeclared", self.transition(required_checks=("absent",)), complete),
            ("branch-mismatch", self.transition(publication_branch="other"), complete),
            ("new-work-intended-branch", self.transition(branch="base"), complete),
            ("duplicate-path", self.transition(paths=("allowed.txt", "allowed.txt")), complete),
            ("duplicate-check", self.transition(checks=(check, check), required_checks=("publication",)), complete),
        )
        for label, transition, host in cases:
            with self.subTest(label=label), \
                 patch("codex_autonomy_runner.runtime_wake.run_runtime_worker") as worker, \
                 patch("codex_autonomy_runner.runtime_wake.publish_validated_worker_work") as publication:
                report = self.invoke(response=CoordinatorResponse(CoordinatorDisposition.TRANSITION, transition), host=host)
            self.assertIs(report.result.outcome, InvocationOutcome.RUNTIME_EXECUTION_FAILURE)
            self.assertEqual(report.evidence.worker_attempts, 0)
            worker.assert_not_called(); publication.assert_not_called()
            self.assertTrue(report.invocation.evidence.lock_released)

    def test_execution_profile_has_one_source_and_reaches_worker_by_identity(self):
        profile = object()
        response = CoordinatorResponse(CoordinatorDisposition.TRANSITION, self.transition())
        seen = []
        def worker(*args, **kwargs):
            seen.append(kwargs["execution_profile"])
            return self.worker(RuntimeWorkerStatus.VALIDATED)
        with patch("codex_autonomy_runner.runtime_wake.run_runtime_worker", side_effect=worker), \
             patch("codex_autonomy_runner.runtime_wake.publish_validated_worker_work", return_value=self.publication(RuntimePublicationStatus.VERIFIED, InvocationOutcome.COMPLETED_AUTHORIZED_TRANSITION)):
            report = self.invoke(response=response, execution_profile=profile)
        self.assertEqual(seen, [profile])
        self.assertNotIn("execution_profile", repr(report))
        self.assertNotIn("execution_profile", repr(report.evidence))

    def test_directed_finalizer_is_prepass_once_with_no_postpass(self):
        directed = DurableWakeState(object(), finalizer_context=object(), durable_transport=object())
        response = CoordinatorResponse(CoordinatorDisposition.TRANSITION, self.transition())
        def finalizer(*args, **kwargs):
            self.events.append("finalizer")
            return self.prepass(FinalizerPrepassStatus.NO_FINALIZATION, InvocationOutcome.NO_OP)
        with patch("codex_autonomy_runner.runtime_wake.run_finalizer_prepass", side_effect=finalizer) as finalizer_call, \
             patch("codex_autonomy_runner.runtime_wake.run_runtime_worker", return_value=self.worker(RuntimeWorkerStatus.VALIDATED)) as worker, \
             patch("codex_autonomy_runner.runtime_wake.publish_validated_worker_work", return_value=self.publication(RuntimePublicationStatus.VERIFIED, InvocationOutcome.COMPLETED_AUTHORIZED_TRANSITION)) as publication:
            report = self.invoke([directed], response)
        self.assertIs(report.result.outcome, InvocationOutcome.COMPLETED_AUTHORIZED_TRANSITION)
        finalizer_call.assert_called_once(); worker.assert_called_once(); publication.assert_called_once()
        self.assertEqual(self.events, ["refresh", "finalizer", "coordinator"])

    def test_coordinator_worker_and_publication_exceptions_fail_closed(self):
        response = CoordinatorResponse(CoordinatorDisposition.TRANSITION, self.transition())
        cases = (
            ("coordinator", None, RuntimeError("coordinator secret")),
            ("worker", "worker", RuntimeError("worker secret")),
            ("publication", "publication", RuntimeError("publication secret")),
        )
        for name, stage, error in cases:
            with self.subTest(name=name):
                if stage is None:
                    coordinator = type("BrokenCoordinator", (), {"coordinate": lambda *_: (_ for _ in ()).throw(error)})()
                    report = run_repository_wake(self.request, Refresher([self.empty], self.events), coordinator, self.host)
                else:
                    with patch("codex_autonomy_runner.runtime_wake.run_runtime_worker", side_effect=error if stage == "worker" else self.worker(RuntimeWorkerStatus.VALIDATED)) as worker, \
                         patch("codex_autonomy_runner.runtime_wake.publish_validated_worker_work", side_effect=error if stage == "publication" else None) as publication:
                        report = self.invoke(response=response)
                    if stage == "worker": publication.assert_not_called()
                self.assertIs(report.result.outcome, InvocationOutcome.INTERRUPTED_OR_UNTRUSTWORTHY)
                self.assertTrue(report.invocation.evidence.lock_released)
                self.assertNotIn("secret", repr(report.evidence))

    def test_recovery_possible_prior_worker_is_terminal_without_coordinator_or_worker(self):
        uncertain = DurableWakeState(object(), recovery=RecoveryEvidence(True, True))
        with patch("codex_autonomy_runner.runtime_wake.run_runtime_worker") as worker:
            report = self.invoke([uncertain])
        self.assertIs(report.result.outcome, InvocationOutcome.INTERRUPTED_OR_UNTRUSTWORTHY)
        self.assertEqual(self.events, ["refresh"])
        worker.assert_not_called()

    def test_exceptions_fail_closed_and_lock_releases(self):
        class Broken:
            def refresh(self, context): raise RuntimeError("private error")
        report = run_repository_wake(self.request, Broken(), Coordinator(
            CoordinatorResponse(CoordinatorDisposition.NO_OP), self.events), self.host)
        self.assertIs(report.result.outcome, InvocationOutcome.INTERRUPTED_OR_UNTRUSTWORTHY)
        self.assertTrue(report.invocation.evidence.lock_released)
        self.assertNotIn("private error", repr(report.evidence))

    def test_evidence_is_sanitized_and_clock_is_deterministic(self):
        values = iter((10.0, 13.5))
        report = self.invoke(clock=lambda: next(values))
        self.assertEqual(report.evidence.duration, 3.5)
        rendered = repr(report.evidence)
        for private in ("private instructions", "private profile", str(self.repo), "private error"):
            self.assertNotIn(private, rendered)


if __name__ == "__main__":
    unittest.main()
