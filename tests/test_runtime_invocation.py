"""RUNTIME-01 checks using disposable local repositories, never a live worker."""

from dataclasses import FrozenInstanceError, fields, replace
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from codex_autonomy_runner.invocation_contract import (
    InvocationOutcome, InvocationRequest, InvocationResult,
)
from codex_autonomy_runner.native_process import run_native_process
from codex_autonomy_runner.repository_inspection import inspect_repository
from codex_autonomy_runner.runtime_invocation import (
    ControlledInvocationFailure, InvocationStage, run_repository_invocation,
)


def no_op(context):
    return InvocationResult(InvocationOutcome.NO_OP)


class RuntimeInvocationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repository = self.create_repository("repo")
        self.request = InvocationRequest(self.repository, "main")

    def git(self, repository, *args):
        result = run_native_process(("git", *args), cwd=repository)
        self.assertEqual(0, result.returncode, result.stderr)
        return result.stdout.strip()

    def create_repository(self, name):
        repository = Path(self.temporary.name) / name
        repository.mkdir()
        self.git(repository, "init")
        self.git(repository, "-c", "user.name=Test User", "-c",
                 "user.email=test@example.invalid", "commit", "--allow-empty", "-m", "fixture")
        return repository

    def invoke(self, action=no_op, **kwargs):
        return run_repository_invocation(self.request, action, **kwargs)

    def assert_result(self, report, outcome):
        self.assertIs(report.result.outcome, outcome)

    def test_lock_is_held_during_action_released_and_worktree_unchanged(self):
        before = inspect_repository(self.repository)
        lock_path = self.repository / ".git" / "codex-autonomy-runner.lock"

        def action(context):
            self.assertTrue(lock_path.is_dir())
            self.assertEqual(before, context.inspection)
            self.assertEqual(before, inspect_repository(self.repository))
            self.assertEqual([], list(lock_path.iterdir()))
            return no_op(context)

        report = self.invoke(action)
        self.assert_result(report, InvocationOutcome.NO_OP)
        self.assertTrue(report.evidence.lock_acquired)
        self.assertTrue(report.evidence.lock_released)
        self.assertTrue(report.evidence.action_started)
        self.assertTrue(report.evidence.completion_reliable)
        self.assertFalse(lock_path.exists())
        self.assertEqual(before, inspect_repository(self.repository))
        self.assert_result(self.invoke(), InvocationOutcome.NO_OP)

    def test_same_repository_contends_and_does_not_enter_action(self):
        second_action = Mock(side_effect=AssertionError("must not enter"))

        def action(context):
            second = self.invoke(second_action)
            self.assert_result(second, InvocationOutcome.RUNTIME_EXECUTION_FAILURE)
            self.assertIs(second.evidence.failure_stage, InvocationStage.LOCK)
            self.assertFalse(second.evidence.lock_acquired)
            self.assertFalse(second.evidence.action_started)
            second_action.assert_not_called()
            return no_op(context)

        self.assert_result(self.invoke(action), InvocationOutcome.NO_OP)

    def test_contention_across_real_processes_without_sleep(self):
        # The parent holds the lock synchronously until CORE-01's child exits.
        # No scheduling race or timing threshold is used to establish overlap.
        program = (
            "from pathlib import Path; import sys; "
            "from codex_autonomy_runner import InvocationRequest, InvocationResult, "
            "InvocationOutcome, run_repository_invocation; "
            "r=run_repository_invocation(InvocationRequest(Path(sys.argv[1]), 'main'), "
            "lambda ctx: InvocationResult(InvocationOutcome.NO_OP)); "
            "print(r.result.outcome.value); print(r.evidence.action_started)"
        )

        def action(context):
            child = run_native_process(
                (sys.executable, "-B", "-c", program, str(self.repository)),
                cwd=Path(__file__).resolve().parents[1],
            )
            self.assertEqual(0, child.returncode, child.stderr)
            self.assertEqual("runtime_execution_failure\nFalse\n", child.stdout)
            return no_op(context)

        self.assert_result(self.invoke(action), InvocationOutcome.NO_OP)

    def test_distinct_repositories_can_both_hold_locks(self):
        other = InvocationRequest(self.create_repository("other"), "main")
        nested = []

        def action(context):
            nested.append(run_repository_invocation(other, no_op))
            return no_op(context)

        first = self.invoke(action)
        self.assert_result(first, InvocationOutcome.NO_OP)
        self.assert_result(nested[0], InvocationOutcome.NO_OP)
        self.assertNotEqual(first.evidence.repository_id, nested[0].evidence.repository_id)

    def test_subdirectory_and_linked_worktree_share_repository_lock(self):
        nested = self.repository / "nested"
        nested.mkdir()
        linked = Path(self.temporary.name) / "linked"
        self.git(self.repository, "worktree", "add", "--detach", str(linked), "HEAD")

        def action(context):
            for path in (nested, linked):
                second = run_repository_invocation(InvocationRequest(path, "main"), no_op)
                self.assert_result(second, InvocationOutcome.RUNTIME_EXECUTION_FAILURE)
                self.assertIs(second.evidence.failure_stage, InvocationStage.LOCK)
                self.assertFalse(second.evidence.action_started)
            return no_op(context)

        self.assert_result(self.invoke(action), InvocationOutcome.NO_OP)

    def test_controlled_failure_releases_lock_and_excludes_exception_text(self):
        def action(context):
            raise ControlledInvocationFailure("sensitive diagnostic")

        report = self.invoke(action)
        self.assert_result(report, InvocationOutcome.RUNTIME_EXECUTION_FAILURE)
        self.assertTrue(report.evidence.completion_reliable)
        self.assertTrue(report.evidence.lock_released)
        self.assertIs(report.evidence.failure_stage, InvocationStage.ACTION)
        self.assertNotIn("sensitive diagnostic", repr(report))
        self.assert_result(self.invoke(), InvocationOutcome.NO_OP)

    def test_unexpected_exception_is_uncertain_and_not_retried(self):
        action = Mock(side_effect=RuntimeError("private data"))
        report = self.invoke(action)
        self.assert_result(report, InvocationOutcome.INTERRUPTED_OR_UNTRUSTWORTHY)
        self.assertFalse(report.evidence.completion_reliable)
        self.assertTrue(report.evidence.lock_released)
        action.assert_called_once()
        self.assertNotIn("private data", repr(report))
        self.assert_result(self.invoke(), InvocationOutcome.NO_OP)

    def test_keyboard_interrupt_and_system_exit_are_explicit_and_release_lock(self):
        for interruption in (KeyboardInterrupt(), SystemExit(2)):
            with self.subTest(interruption=type(interruption)):
                report = self.invoke(Mock(side_effect=interruption))
                self.assert_result(report, InvocationOutcome.INTERRUPTED_OR_UNTRUSTWORTHY)
                self.assertTrue(report.evidence.action_started)
                self.assertFalse(report.evidence.completion_reliable)
                self.assertTrue(report.evidence.lock_released)
                self.assert_result(self.invoke(), InvocationOutcome.NO_OP)

    def test_caller_results_are_preserved_without_inventing_semantic_completion(self):
        for outcome in InvocationOutcome:
            with self.subTest(outcome=outcome):
                supplied = InvocationResult(outcome)
                report = self.invoke(lambda context: supplied)
                self.assertIs(supplied, report.result)
                self.assertEqual(
                    outcome is not InvocationOutcome.INTERRUPTED_OR_UNTRUSTWORTHY,
                    report.evidence.completion_reliable,
                )

    def test_invalid_action_result_is_uncertain(self):
        for value in (None, "success", InvocationResult("no_op")):
            with self.subTest(value=value):
                report = self.invoke(lambda context: value)
                self.assert_result(report, InvocationOutcome.INTERRUPTED_OR_UNTRUSTWORTHY)
                self.assertFalse(report.evidence.completion_reliable)
                self.assertTrue(report.evidence.lock_released)

    def test_invalid_repository_or_unborn_head_is_technical_failure(self):
        unborn = Path(self.temporary.name) / "unborn"
        unborn.mkdir()
        self.git(unborn, "init")
        for path in (Path(self.temporary.name), unborn, unborn / "absent"):
            action = Mock()
            report = run_repository_invocation(InvocationRequest(path, "main"), action)
            self.assert_result(report, InvocationOutcome.RUNTIME_EXECUTION_FAILURE)
            self.assertIs(report.evidence.failure_stage, InvocationStage.REPOSITORY)
            self.assertFalse(report.evidence.lock_acquired)
            action.assert_not_called()

    def test_empty_or_non_string_intended_ref_is_rejected(self):
        for ref in ("", " \t", None, 42):
            with self.subTest(ref=ref):
                action = Mock()
                report = run_repository_invocation(InvocationRequest(self.repository, ref), action)
                self.assert_result(report, InvocationOutcome.RUNTIME_EXECUTION_FAILURE)
                self.assertIs(report.evidence.failure_stage, InvocationStage.REQUEST)
                self.assertIsNone(report.evidence.intended_ref)
                action.assert_not_called()

    def test_unresolvable_ref_and_dirty_detached_tree_are_not_semantic_preflight(self):
        self.git(self.repository, "checkout", "--detach")
        (self.repository / "existing.txt").write_text("existing user work", encoding="utf-8")
        before = inspect_repository(self.repository)
        report = run_repository_invocation(
            InvocationRequest(self.repository, "not-yet-resolved-ref"), no_op
        )
        self.assert_result(report, InvocationOutcome.NO_OP)
        self.assertEqual(before, inspect_repository(self.repository))

    def test_inspection_failure_under_lock_releases_without_action(self):
        initial = inspect_repository(self.repository)
        with patch("codex_autonomy_runner.runtime_invocation.inspect_repository",
                   side_effect=(initial, OSError("sensitive failure"))):
            action = Mock()
            report = self.invoke(action)
        self.assert_result(report, InvocationOutcome.RUNTIME_EXECUTION_FAILURE)
        self.assertIs(report.evidence.failure_stage, InvocationStage.PREFLIGHT)
        self.assertTrue(report.evidence.lock_acquired)
        self.assertTrue(report.evidence.lock_released)
        action.assert_not_called()
        self.assert_result(self.invoke(), InvocationOutcome.NO_OP)

    def test_repository_movement_during_preflight_is_rejected(self):
        initial = inspect_repository(self.repository)
        moved = replace(initial, root=Path(self.temporary.name))
        with patch("codex_autonomy_runner.runtime_invocation.inspect_repository",
                   side_effect=(initial, moved)):
            report = self.invoke()
        self.assert_result(report, InvocationOutcome.RUNTIME_EXECUTION_FAILURE)
        self.assertIs(report.evidence.failure_stage, InvocationStage.PREFLIGHT)
        self.assertTrue(report.evidence.lock_released)

    def test_occupied_or_ambiguous_lock_entry_is_never_stolen(self):
        lock_path = self.repository / ".git" / "codex-autonomy-runner.lock"
        lock_path.write_text("unknown owner", encoding="utf-8")
        action = Mock()
        report = self.invoke(action)
        self.assert_result(report, InvocationOutcome.RUNTIME_EXECUTION_FAILURE)
        self.assertEqual("unknown owner", lock_path.read_text(encoding="utf-8"))
        action.assert_not_called()

    def test_lock_permission_failure_is_technical_and_does_not_enter(self):
        with patch("codex_autonomy_runner.runtime_invocation._RepositoryLock.acquire",
                   side_effect=PermissionError("private path")):
            report = self.invoke()
        self.assert_result(report, InvocationOutcome.RUNTIME_EXECUTION_FAILURE)
        self.assertIs(report.evidence.failure_stage, InvocationStage.LOCK)
        self.assertFalse(report.evidence.action_started)

    def test_separate_git_directory_in_visible_tree_is_rejected_without_artifacts(self):
        repository = Path(self.temporary.name) / "visible-metadata"
        repository.mkdir()
        metadata = repository / "administration"
        self.git(repository, "init", "--separate-git-dir", str(metadata))
        self.git(repository, "-c", "user.name=Test User", "-c",
                 "user.email=test@example.invalid", "commit", "--allow-empty", "-m", "fixture")
        before = inspect_repository(repository)
        report = run_repository_invocation(InvocationRequest(repository, "main"), no_op)
        self.assert_result(report, InvocationOutcome.RUNTIME_EXECUTION_FAILURE)
        self.assertFalse(report.evidence.lock_acquired)
        self.assertEqual(before, inspect_repository(repository))
        self.assertFalse((metadata / "codex-autonomy-runner.lock").exists())

    def test_replaced_lock_is_not_deleted(self):
        lock_path = self.repository / ".git" / "codex-autonomy-runner.lock"

        def action(context):
            lock_path.rename(lock_path.with_name("original-lock"))
            lock_path.mkdir()
            return no_op(context)

        report = self.invoke(action)
        self.assert_result(report, InvocationOutcome.INTERRUPTED_OR_UNTRUSTWORTHY)
        self.assertIs(report.evidence.failure_stage, InvocationStage.RELEASE)
        self.assertFalse(report.evidence.lock_released)
        self.assertTrue(lock_path.is_dir())

    def test_interrupted_preflight_releases_lock_without_starting_action(self):
        initial = inspect_repository(self.repository)
        with patch("codex_autonomy_runner.runtime_invocation.inspect_repository",
                   side_effect=(initial, KeyboardInterrupt())):
            report = self.invoke()
        self.assert_result(report, InvocationOutcome.INTERRUPTED_OR_UNTRUSTWORTHY)
        self.assertFalse(report.evidence.action_started)
        self.assertTrue(report.evidence.lock_acquired)
        self.assertTrue(report.evidence.lock_released)
        self.assert_result(self.invoke(), InvocationOutcome.NO_OP)

    def test_release_failure_overrides_success_without_removing_unknown_contents(self):
        lock_path = self.repository / ".git" / "codex-autonomy-runner.lock"

        def action(context):
            (lock_path / "unexpected").write_text("do not delete", encoding="utf-8")
            return no_op(context)

        report = self.invoke(action)
        self.assert_result(report, InvocationOutcome.INTERRUPTED_OR_UNTRUSTWORTHY)
        self.assertIs(report.evidence.failure_stage, InvocationStage.RELEASE)
        self.assertTrue(report.evidence.completion_reliable)  # action did return
        self.assertFalse(report.evidence.lock_released)
        self.assertTrue((lock_path / "unexpected").exists())
        self.assert_result(self.invoke(), InvocationOutcome.RUNTIME_EXECUTION_FAILURE)

    def test_profile_is_passed_by_identity_without_interpretation_or_evidence(self):
        class OpaqueProfile:
            def __repr__(self):
                raise AssertionError("must never render profile")

        token = OpaqueProfile()

        def action(context):
            self.assertIs(token, context.execution_profile)
            self.assertNotIn("execution_profile", repr(context))
            return no_op(context)

        report = self.invoke(action, execution_profile=token)
        self.assert_result(report, InvocationOutcome.NO_OP)
        self.assertNotIn("execution_profile", repr(report))

    def test_evidence_is_immutable_stable_and_minimal(self):
        first, second = self.invoke(), self.invoke()
        self.assertEqual(first, second)
        self.assertEqual(64, len(first.evidence.repository_id))
        self.assertEqual("main", first.evidence.intended_ref)
        self.assertNotIn(str(self.repository), repr(first))
        self.assertEqual(
            {"repository_id", "intended_ref", "lock_acquired", "lock_released",
             "action_started", "completion_reliable", "failure_stage"},
            {item.name for item in fields(first.evidence)},
        )
        with self.assertRaises(FrozenInstanceError):
            first.evidence.lock_acquired = False

    def test_runtime_uses_only_local_read_only_git_commands_and_no_network(self):
        with patch("socket.create_connection", side_effect=AssertionError("no network")), \
             patch("codex_autonomy_runner.native_process.subprocess.run",
                   wraps=subprocess.run) as processes:
            report = self.invoke()
        self.assert_result(report, InvocationOutcome.NO_OP)
        self.assertGreater(len(processes.call_args_list), 0)
        for call in processes.call_args_list:
            argv = call.args[0]
            self.assertEqual("git", argv[0])
            self.assertIn(argv[1], ("rev-parse", "symbolic-ref", "diff", "ls-files"))
            self.assertFalse(call.kwargs["shell"])


if __name__ == "__main__":
    unittest.main()
