"""Temporary repositories and fake remote/PR transport for RUNTIME-04."""

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from codex_autonomy_runner.execution_baseline import IntendedRefObservation
from codex_autonomy_runner.existing_work import ExistingWorkObservation, discover_existing_work
from codex_autonomy_runner.invocation_contract import InvocationOutcome, InvocationRequest
from codex_autonomy_runner.native_process import NativeProcessResult, run_native_process
from codex_autonomy_runner.repository_inspection import inspect_repository
from codex_autonomy_runner.runtime_invocation import repository_identity
from codex_autonomy_runner.runtime_publication import (
    PublicationPullRequest, PushCompletion, RemoteBranchObservation,
    RuntimePublicationRequest, RuntimePublicationStatus, publish_validated_worker_work,
)
from codex_autonomy_runner.runtime_worker import (
    CheckCompletion, CheckDeclaration, CheckEvidence, CheckKind, RuntimeWorkerStatus,
    WorkerCompletion, run_runtime_worker,
)


class Worker:
    def __init__(self, action):
        self.action = action
        self.calls = 0

    def execute(self, context):
        self.calls += 1
        return self.action(context)


class Checks:
    def execute(self, context):
        return CheckCompletion(0)


class Transport:
    def __init__(self):
        self.remote = {}
        self.pushes = []
        self.created = []
        self.observed = []
        self.pr_observation_push_counts = []
        self.prs = {}
        self.push_completion = PushCompletion(True)
        self.observe_error = None
        self.create_error = None
        self.wrong_pr = False
        self.pr_observer = None

    def observe_remote_branch(self, repository_id, branch):
        if self.observe_error and self.pushes:
            raise self.observe_error
        head = self.remote.get((repository_id, branch))
        return RemoteBranchObservation(head is not None, head)

    def push_non_force(self, repository, branch, commit_sha):
        self.pushes.append((repository, branch, commit_sha))
        if self.push_completion.completed:
            self.remote[(self.repository_id, branch)] = commit_sha
            for number, pr in tuple(self.prs.items()):
                if pr.repository_id == self.repository_id and pr.head_branch == branch:
                    self.prs[number] = replace(pr, head_sha=commit_sha)
        return self.push_completion

    def create_pull_request(self, request):
        self.created.append(request)
        if self.create_error:
            raise self.create_error
        pr = PublicationPullRequest(71, request.repository_id, request.base_ref,
                                    request.head_branch, request.head_sha, True)
        self.prs[71] = pr
        return pr

    def observe_pull_request(self, repository_id, number):
        self.observed.append((repository_id, number))
        self.pr_observation_push_counts.append(len(self.pushes))
        if self.pr_observer:
            return self.pr_observer(repository_id, number)
        result = self.prs.get(number)
        if self.wrong_pr and result:
            return replace(result, head_sha="f" * 40)
        if result is None:
            raise RuntimeError("missing fake pr")
        return result


class RuntimePublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        self.git("init", "-b", "base")
        (self.repo / "allowed.txt").write_text("before\n", encoding="utf-8")
        (self.repo / "other.txt").write_text("other\n", encoding="utf-8")
        self.git("add", "--", "allowed.txt", "other.txt")
        self.commit("fixture")
        self.head = self.git("rev-parse", "HEAD")
        self.request = InvocationRequest(self.repo, "base")

    def git(self, *args):
        result = run_native_process(("git", *args), cwd=self.repo)
        self.assertEqual(0, result.returncode, result.stderr)
        return result.stdout.strip()

    def commit(self, message):
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                 "commit", "-m", message)

    def worker_result(self, *, existing=False, check=True):
        discovery = discover_existing_work("CHECKPOINT", ())
        branch = "caller/work"
        if existing:
            branch = "existing/work"
            self.git("branch", branch, self.head)
            self.git("switch", branch)
            discovery = discover_existing_work("CHECKPOINT", (
                ExistingWorkObservation("CHECKPOINT", 42, branch, self.head, True),
            ))
        def change(context):
            (context.repository / "allowed.txt").write_text("published\n", encoding="utf-8")
            return WorkerCompletion(True)
        checks = (CheckDeclaration("publication", CheckKind.PUBLICATION, ("fake",)),) if check else ()
        result = run_runtime_worker(
            self.request, "CHECKPOINT", IntendedRefObservation("base", self.head), discovery,
            Worker(change), permitted_paths=("allowed.txt",), checks=checks,
            check_executor=Checks(), new_branch=branch, attempt_id="attempt",
        )
        self.assertIs(result.status, RuntimeWorkerStatus.VALIDATED)
        return result

    def publication_request(self, result, *, required=("publication",)):
        identity = repository_identity(inspect_repository(self.repo))
        return RuntimePublicationRequest(
            self.repo, identity, result.post_worker.branch, ("allowed.txt",), required,
            "caller supplied message", "base", "caller title", "caller body",
        )

    def transport(self, result, *, existing=False):
        transport = Transport()
        transport.repository_id = repository_identity(inspect_repository(self.repo))
        if existing:
            transport.remote[(transport.repository_id, result.post_worker.branch)] = self.head
            transport.prs[42] = PublicationPullRequest(42, transport.repository_id, "base",
                                                       result.post_worker.branch, self.head, True)
        return transport

    def test_rejects_nonvalidated_or_missing_required_check(self):
        result = self.worker_result()
        transport = self.transport(result)
        invalid = replace(result, status=RuntimeWorkerStatus.FOCUSED_UNSATISFIED)
        outcome = publish_validated_worker_work(self.publication_request(result), invalid, transport)
        self.assertIs(outcome.status, RuntimePublicationStatus.REFUSED)
        missing = publish_validated_worker_work(self.publication_request(result, required=("missing",)), result, transport)
        self.assertIs(missing.status, RuntimePublicationStatus.REFUSED)
        self.assertFalse(transport.pushes)

    def test_rejects_unsatisfied_or_duplicate_publication_evidence(self):
        result = self.worker_result()
        transport = self.transport(result)
        failed = replace(result, publication_checks=(
            CheckEvidence("publication", CheckKind.PUBLICATION, 1),
        ))
        duplicate = replace(result, publication_checks=(
            result.publication_checks[0], result.publication_checks[0],
        ))
        for inconsistent in (failed, duplicate):
            with self.subTest(inconsistent=inconsistent.publication_checks):
                outcome = publish_validated_worker_work(
                    self.publication_request(result), inconsistent, transport
                )
                self.assertIs(outcome.status, RuntimePublicationStatus.REFUSED)
        self.assertFalse(transport.pushes)

    def test_empty_required_checks_is_explicit_not_inferred(self):
        result = self.worker_result(check=False)
        transport = self.transport(result)
        outcome = publish_validated_worker_work(self.publication_request(result, required=()), result, transport)
        self.assertTrue(outcome.publication_verified)

    def test_required_pr_inputs_are_refused_before_any_mutation(self):
        result = self.worker_result()
        transport = self.transport(result)
        request = self.publication_request(result)
        for invalid in (
            replace(request, base_ref=None),
            replace(request, base_ref=""),
            replace(request, pr_title=None),
            replace(request, pr_title=""),
            replace(request, pr_body=None),
        ):
            with self.subTest(invalid=invalid):
                outcome = publish_validated_worker_work(invalid, result, transport)
                self.assertIs(outcome.status, RuntimePublicationStatus.REFUSED)
                self.assertEqual((), inspect_repository(self.repo).changed_paths.staged)
                self.assertEqual(self.head, self.git("rev-parse", "HEAD"))
        self.assertFalse(transport.pushes)
        self.assertFalse(transport.created)

    def test_empty_pr_body_is_explicitly_valid(self):
        result = self.worker_result()
        transport = self.transport(result)
        outcome = publish_validated_worker_work(
            replace(self.publication_request(result), pr_body=""), result, transport
        )
        self.assertTrue(outcome.publication_verified)

    def test_same_path_content_change_after_checks_is_stale_before_stage(self):
        result = self.worker_result()
        (self.repo / "allowed.txt").write_text("after-check\n", encoding="utf-8")
        transport = self.transport(result)
        outcome = publish_validated_worker_work(self.publication_request(result), result, transport)
        self.assertIs(outcome.status, RuntimePublicationStatus.REFUSED)
        self.assertEqual((), inspect_repository(self.repo).changed_paths.staged)
        self.assertFalse(transport.pushes)

    def test_missing_fingerprint_or_repository_identity_refuses_before_mutation(self):
        result = self.worker_result()
        transport = self.transport(result)
        missing = replace(result, worktree_fingerprint=None)
        outcome = publish_validated_worker_work(self.publication_request(result), missing, transport)
        self.assertIs(outcome.status, RuntimePublicationStatus.REFUSED)
        wrong = replace(self.publication_request(result), repository_id="0" * 64)
        outcome = publish_validated_worker_work(wrong, result, transport)
        self.assertIs(outcome.status, RuntimePublicationStatus.REFUSED)
        self.assertFalse(transport.pushes)

    def test_refuses_invalid_paths_staged_changes_identity_or_branch_movement(self):
        for mutate in (
            lambda: (self.repo / "other.txt").write_text("bad", encoding="utf-8"),
            lambda: self.git("add", "allowed.txt"),
            lambda: self.git("switch", "base"),
        ):
            with self.subTest(mutate=mutate):
                # Each subcase needs its own fixture because invalid state stays preserved.
                self.tearDown(); self.setUp()
                result = self.worker_result()
                mutate()
                outcome = publish_validated_worker_work(self.publication_request(result), result, self.transport(result))
                self.assertIs(outcome.status, RuntimePublicationStatus.REFUSED)

    def test_new_work_exact_stage_commit_push_and_pr_are_verified(self):
        result = self.worker_result()
        transport = self.transport(result)
        outcome = publish_validated_worker_work(self.publication_request(result), result, transport)
        self.assertTrue(outcome.publication_verified)
        self.assertEqual(self.head, self.git("rev-parse", "HEAD^"))
        self.assertEqual("allowed.txt", self.git("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"))
        self.assertEqual(1, len(transport.pushes))
        self.assertEqual(1, len(transport.created))
        self.assertEqual(outcome.local_commit_sha, outcome.remote_head_sha)
        self.assertTrue(outcome.pull_request.is_open)

    def test_existing_work_updates_exact_pr_without_second_creation(self):
        result = self.worker_result(existing=True)
        transport = self.transport(result, existing=True)
        request = self.publication_request(result)
        outcome = publish_validated_worker_work(request, result, transport)
        self.assertTrue(outcome.publication_verified)
        self.assertEqual([], transport.created)
        self.assertEqual(42, outcome.pull_request.number)
        self.assertEqual([(transport.repository_id, 42), (transport.repository_id, 42)],
                         transport.observed)
        self.assertEqual([0, 1], transport.pr_observation_push_counts)

    def test_existing_pr_identity_failures_refuse_before_staging(self):
        result = self.worker_result(existing=True)
        request = self.publication_request(result)
        expected = self.transport(result, existing=True).prs[42]
        invalid = (
            replace(expected, is_open=False),
            replace(expected, base_ref="wrong-base"),
            replace(expected, head_branch="wrong-branch"),
            replace(expected, head_sha="a" * 40),
            replace(expected, number=99),
        )
        for observation in invalid:
            with self.subTest(observation=observation):
                transport = self.transport(result, existing=True)
                transport.prs[42] = observation
                outcome = publish_validated_worker_work(request, result, transport)
                self.assertIs(outcome.status, RuntimePublicationStatus.REFUSED)
                self.assertEqual(self.head, self.git("rev-parse", "HEAD"))
                self.assertEqual((), inspect_repository(self.repo).changed_paths.staged)
                self.assertFalse(transport.pushes)
                self.assertFalse(transport.created)
                self.assertEqual([(transport.repository_id, 42)], transport.observed)

    def test_existing_pr_observation_exception_refuses_before_staging(self):
        result = self.worker_result(existing=True)
        transport = self.transport(result, existing=True)
        def unavailable(repository_id, number):
            raise RuntimeError("fixture")
        transport.pr_observer = unavailable
        outcome = publish_validated_worker_work(self.publication_request(result), result, transport)
        self.assertIs(outcome.status, RuntimePublicationStatus.REFUSED)
        self.assertEqual(self.head, self.git("rev-parse", "HEAD"))
        self.assertEqual((), inspect_repository(self.repo).changed_paths.staged)
        self.assertFalse(transport.pushes)

    def test_new_remote_collision_and_existing_remote_movement_refuse_precommit(self):
        result = self.worker_result()
        transport = self.transport(result)
        transport.remote[(transport.repository_id, result.post_worker.branch)] = self.head
        outcome = publish_validated_worker_work(self.publication_request(result), result, transport)
        self.assertIs(outcome.status, RuntimePublicationStatus.REFUSED)
        self.assertEqual(self.head, self.git("rev-parse", "HEAD"))
        self.tearDown(); self.setUp()
        result = self.worker_result(existing=True)
        transport = self.transport(result, existing=True)
        transport.remote[(transport.repository_id, result.post_worker.branch)] = "a" * 40
        outcome = publish_validated_worker_work(self.publication_request(result), result, transport)
        self.assertIs(outcome.status, RuntimePublicationStatus.REFUSED)

    def test_malformed_remote_observation_refuses_before_commit(self):
        result = self.worker_result()
        transport = self.transport(result)
        transport.observe_remote_branch = lambda repository_id, branch: RemoteBranchObservation(False, self.head)
        outcome = publish_validated_worker_work(self.publication_request(result), result, transport)
        self.assertIs(outcome.status, RuntimePublicationStatus.REFUSED)
        self.assertEqual(self.head, self.git("rev-parse", "HEAD"))

    def test_push_failure_preserves_committed_but_unpublished_without_retry(self):
        result = self.worker_result()
        transport = self.transport(result)
        transport.push_completion = PushCompletion(False)
        outcome = publish_validated_worker_work(self.publication_request(result), result, transport)
        self.assertIs(outcome.status, RuntimePublicationStatus.COMMITTED_UNPUBLISHED)
        self.assertIsNotNone(outcome.local_commit_sha)
        self.assertEqual(1, len(transport.pushes))
        self.assertEqual([], transport.created)
        self.assertIs(outcome.invocation_result.outcome, InvocationOutcome.RUNTIME_EXECUTION_FAILURE)

    def test_failed_add_preserves_index_and_is_staging_untrustworthy(self):
        result = self.worker_result()
        transport = self.transport(result)
        real = run_native_process
        add_calls = []
        def process(argv, **kwargs):
            observed = real(argv, **kwargs)
            if argv[:2] == ("git", "add"):
                add_calls.append(argv)
                return NativeProcessResult(tuple(argv), 1, observed.stdout, "fixture failure")
            return observed
        with patch("codex_autonomy_runner.runtime_publication.run_native_process", side_effect=process):
            outcome = publish_validated_worker_work(self.publication_request(result), result, transport)
        self.assertIs(outcome.status, RuntimePublicationStatus.STAGING_UNTRUSTWORTHY)
        self.assertIs(outcome.invocation_result.outcome, InvocationOutcome.INTERRUPTED_OR_UNTRUSTWORTHY)
        self.assertEqual(1, len(add_calls))
        self.assertEqual(("allowed.txt",), inspect_repository(self.repo).changed_paths.staged)
        self.assertFalse(transport.pushes)
        self.assertFalse(transport.created)

    def test_post_stage_discrepancy_is_not_refused_and_is_preserved(self):
        result = self.worker_result()
        transport = self.transport(result)
        real = run_native_process
        def process(argv, **kwargs):
            observed = real(argv, **kwargs)
            if argv[:2] == ("git", "add"):
                (self.repo / "allowed.txt").write_text("changed-after-stage", encoding="utf-8")
            return observed
        with patch("codex_autonomy_runner.runtime_publication.run_native_process", side_effect=process):
            outcome = publish_validated_worker_work(self.publication_request(result), result, transport)
        self.assertIs(outcome.status, RuntimePublicationStatus.STAGING_UNTRUSTWORTHY)
        inspection = inspect_repository(self.repo)
        self.assertEqual(("allowed.txt",), inspection.changed_paths.staged)
        self.assertEqual(("allowed.txt",), inspection.changed_paths.unstaged)
        self.assertFalse(transport.pushes)

    def test_remote_mismatch_or_observation_uncertainty_is_untrustworthy(self):
        result = self.worker_result()
        transport = self.transport(result)
        original = transport.push_non_force
        def push_without_matching_remote(*args):
            original(*args)
            transport.remote[(transport.repository_id, result.post_worker.branch)] = "a" * 40
            return PushCompletion(True)
        transport.push_non_force = push_without_matching_remote
        outcome = publish_validated_worker_work(self.publication_request(result), result, transport)
        self.assertIs(outcome.status, RuntimePublicationStatus.REMOTE_UNTRUSTWORTHY)
        self.assertIs(outcome.invocation_result.outcome, InvocationOutcome.INTERRUPTED_OR_UNTRUSTWORTHY)

    def test_unreliable_push_and_remote_observation_failure_are_untrustworthy(self):
        result = self.worker_result()
        transport = self.transport(result)
        transport.push_completion = PushCompletion(True, completion_reliable=False)
        outcome = publish_validated_worker_work(self.publication_request(result), result, transport)
        self.assertIs(outcome.status, RuntimePublicationStatus.REMOTE_UNTRUSTWORTHY)
        self.assertEqual(1, len(transport.pushes))

    def test_remote_observation_exception_after_push_is_untrustworthy_no_retry(self):
        result = self.worker_result()
        transport = self.transport(result)
        transport.observe_error = RuntimeError("fixture")
        outcome = publish_validated_worker_work(self.publication_request(result), result, transport)
        self.assertIs(outcome.status, RuntimePublicationStatus.REMOTE_UNTRUSTWORTHY)
        self.assertEqual(1, len(transport.pushes))
        self.assertFalse(transport.created)

    def test_pr_failure_or_post_verification_mismatch_is_untrustworthy(self):
        result = self.worker_result()
        transport = self.transport(result)
        transport.wrong_pr = True
        outcome = publish_validated_worker_work(self.publication_request(result), result, transport)
        self.assertIs(outcome.status, RuntimePublicationStatus.PR_UNTRUSTWORTHY)
        self.assertEqual(1, len(transport.created))

    def test_create_exception_after_remote_verification_is_pr_untrustworthy(self):
        result = self.worker_result()
        transport = self.transport(result)
        transport.create_error = RuntimeError("fixture")
        outcome = publish_validated_worker_work(self.publication_request(result), result, transport)
        self.assertIs(outcome.status, RuntimePublicationStatus.PR_UNTRUSTWORTHY)
        self.assertEqual(outcome.local_commit_sha, outcome.remote_head_sha)
        self.assertEqual(1, len(transport.pushes))
        self.assertEqual(1, len(transport.created))

    def test_existing_work_cannot_be_redirected_to_another_pr_number(self):
        result = self.worker_result(existing=True)
        transport = self.transport(result, existing=True)
        original = transport.observe_pull_request
        def redirect_after_push(repository_id, number):
            observed = original(repository_id, number)
            return replace(observed, number=99) if transport.pushes else observed
        transport.observe_pull_request = redirect_after_push
        outcome = publish_validated_worker_work(self.publication_request(result), result, transport)
        self.assertIs(outcome.status, RuntimePublicationStatus.PR_UNTRUSTWORTHY)
        self.assertEqual(outcome.local_commit_sha, outcome.remote_head_sha)
        self.assertFalse(transport.created)

    def test_commit_failure_happens_before_push_and_no_merge_or_finalizer_surface(self):
        result = self.worker_result()
        transport = self.transport(result)
        # Invalid author configuration makes commit fail deterministically in this fixture.
        real = run_native_process
        def process(argv, **kwargs):
            if argv[:2] == ("git", "commit"):
                return NativeProcessResult(tuple(argv), 1, "", "refused")
            return real(argv, **kwargs)
        with patch("codex_autonomy_runner.runtime_publication.run_native_process", side_effect=process):
            outcome = publish_validated_worker_work(self.publication_request(result), result, transport)
        self.assertIs(outcome.status, RuntimePublicationStatus.COMMIT_FAILED)
        self.assertFalse(transport.pushes)
        self.assertFalse(hasattr(transport, "merge"))

    def test_commit_interruption_is_local_not_remote_uncertainty(self):
        result = self.worker_result()
        transport = self.transport(result)
        import codex_autonomy_runner.runtime_publication as publication
        real_git = publication._git
        def git(root, *arguments):
            if arguments and arguments[0] == "commit":
                raise KeyboardInterrupt()
            return real_git(root, *arguments)
        with patch("codex_autonomy_runner.runtime_publication._git", side_effect=git):
            outcome = publish_validated_worker_work(self.publication_request(result), result, transport)
        self.assertIs(outcome.status, RuntimePublicationStatus.COMMIT_FAILED)
        self.assertIs(outcome.invocation_result.outcome, InvocationOutcome.RUNTIME_EXECUTION_FAILURE)
        self.assertFalse(transport.pushes)
        self.assertEqual(self.head, self.git("rev-parse", "HEAD"))


if __name__ == "__main__":
    unittest.main()
