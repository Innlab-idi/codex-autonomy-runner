"""Temporary Git repositories and fake contained workers; no live services."""

from dataclasses import FrozenInstanceError, fields, replace
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from codex_autonomy_runner.existing_work import ExistingWorkObservation, discover_existing_work
from codex_autonomy_runner.execution_baseline import ExecutionBaselineStatus, IntendedRefObservation
from codex_autonomy_runner.invocation_contract import InvocationOutcome, InvocationRequest
from codex_autonomy_runner.native_process import run_native_process
from codex_autonomy_runner.repository_inspection import inspect_repository
from codex_autonomy_runner.runtime_worker import (
    CheckDeclaration, CheckKind, RuntimeWorkerStatus, WorkerCompletion,
    WorkerContext, run_runtime_worker,
    CheckExecutor, CheckExecutionContext, CheckCompletion,
)


class FakeWorker:
    def __init__(self, action=None):
        self.action = action
        self.contexts = []

    def execute(self, context):
        self.contexts.append(context)
        if self.action:
            return self.action(context)
        return WorkerCompletion(True)


class FakeCheckExecutor:
    """Simulate contained execution; never launch the supplied argv."""

    def __init__(self, action=None):
        self.action = action
        self.contexts = []

    def execute(self, context):
        self.contexts.append(context)
        return self.action(context) if self.action else CheckCompletion(0)


class RuntimeWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        self.git("init", "-b", "base")
        (self.repo / "allowed.txt").write_text("before\n", encoding="utf-8")
        (self.repo / "other.txt").write_text("other\n", encoding="utf-8")
        self.git("add", "--", "allowed.txt", "other.txt")
        self.commit()
        self.head = self.git("rev-parse", "HEAD")
        self.request = InvocationRequest(self.repo, "base")
        self.intended = IntendedRefObservation("base", self.head)
        self.existing = discover_existing_work("CHECKPOINT", ())

    def git(self, *args):
        result = run_native_process(("git", *args), cwd=self.repo)
        self.assertEqual(0, result.returncode, result.stderr)
        return result.stdout.strip()

    def commit(self):
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                 "commit", "-m", "fixture")

    def run_worker(self, worker=None, **changes):
        kwargs = dict(permitted_paths=("allowed.txt", "new.txt"),
                      new_branch="caller/chosen", attempt_id="attempt-1",
                      check_executor=FakeCheckExecutor())
        kwargs.update(changes)
        return run_runtime_worker(self.request, "CHECKPOINT", self.intended,
                                  self.existing, worker or FakeWorker(), **kwargs)

    def edit(self, path, text="after\n"):
        def action(context):
            (context.repository / path).write_text(text, encoding="utf-8")
            return WorkerCompletion(True)
        return FakeWorker(action)

    def use_existing(self, branch="existing"):
        self.git("branch", branch, self.head)
        self.existing = discover_existing_work("CHECKPOINT", (
            ExistingWorkObservation("CHECKPOINT", 42, branch, self.head, True),
        ))

    def check(self, name, kind=CheckKind.FOCUSED, code="pass"):
        return CheckDeclaration(name, kind, (sys.executable, "-B", "-c", code))

    def test_new_work_uses_exact_frozen_head_and_caller_branch(self):
        worker = FakeWorker()
        result = self.run_worker(worker)
        self.assertIs(result.status, RuntimeWorkerStatus.VALIDATED)
        self.assertIs(result.baseline.status, ExecutionBaselineStatus.NEW_WORK)
        self.assertEqual(self.head, result.pre_worker.head_sha)
        self.assertEqual("caller/chosen", result.pre_worker.branch)
        self.assertEqual(1, len(worker.contexts))
        self.assertTrue(result.preparation.is_valid)

    def test_existing_work_preserves_exact_branch_head_and_pr(self):
        self.use_existing()
        result = self.run_worker(new_branch="ignored-caller-name")
        self.assertIs(result.status, RuntimeWorkerStatus.VALIDATED)
        self.assertEqual("existing", result.pre_worker.branch)
        self.assertEqual(self.head, result.pre_worker.head_sha)
        self.assertEqual(42, result.baseline.baseline.pr_number)
        self.assertNotIn("ignored-caller-name", self.git("branch", "--list"))

    def test_new_work_does_not_follow_an_advanced_intended_ref(self):
        (self.repo / "other.txt").write_text("advance", encoding="utf-8")
        self.git("add", "other.txt")
        self.commit()
        advanced = self.git("rev-parse", "HEAD")
        result = self.run_worker()
        self.assertTrue(result.publication_candidate)
        self.assertEqual(self.head, result.pre_worker.head_sha)
        self.assertNotEqual(advanced, result.pre_worker.head_sha)
        self.assertEqual(advanced, self.git("rev-parse", "base"))

    def test_existing_attached_branch_is_continued_without_switch(self):
        self.use_existing()
        self.git("switch", "existing")
        calls = []
        def process(argv, **kwargs):
            calls.append(argv)
            return run_native_process(argv, **kwargs)
        with patch("codex_autonomy_runner.runtime_worker.run_native_process", side_effect=process):
            result = self.run_worker()
        self.assertTrue(result.publication_candidate)
        self.assertFalse(any("switch" in call for call in calls))

    def test_ambiguous_baseline_never_prepares_or_calls_worker(self):
        self.existing = discover_existing_work("CHECKPOINT", (
            ExistingWorkObservation("CHECKPOINT", 1, "one", self.head, True),
            ExistingWorkObservation("CHECKPOINT", 2, "two", self.head, True),
        ))
        worker = FakeWorker()
        with patch("codex_autonomy_runner.runtime_worker._prepare") as prepare:
            result = self.run_worker(worker)
        prepare.assert_not_called()
        self.assertFalse(worker.contexts)
        self.assertIs(result.status, RuntimeWorkerStatus.PREPARATION_FAILED)

    def test_intended_ref_mismatch_never_calls_worker(self):
        self.intended = IntendedRefObservation("different", self.head)
        worker = FakeWorker()
        result = self.run_worker(worker)
        self.assertFalse(worker.contexts)
        self.assertIs(result.baseline.status, ExecutionBaselineStatus.INPUT_MISMATCH)

    def test_dirty_preparation_does_not_switch_or_invoke(self):
        (self.repo / "dirty.txt").write_text("keep", encoding="utf-8")
        worker = FakeWorker()
        result = self.run_worker(worker)
        self.assertIs(result.status, RuntimeWorkerStatus.PREPARATION_FAILED)
        self.assertFalse(worker.contexts)
        self.assertEqual("base", self.git("branch", "--show-current"))
        self.assertTrue((self.repo / "dirty.txt").exists())

    def test_wrong_prepared_head_is_rejected_by_core09(self):
        worker = FakeWorker()
        initial = inspect_repository(self.repo)
        moved = replace(initial, branch="caller/chosen", head_sha="b" * 40)
        with patch("codex_autonomy_runner.runtime_worker.inspect_repository",
                   side_effect=(initial, moved)):
            result = self.run_worker(worker)
        self.assertFalse(result.preparation.is_valid)
        self.assertFalse(worker.contexts)

    def test_divergent_existing_branch_is_never_reset(self):
        self.use_existing()
        self.git("switch", "existing")
        (self.repo / "other.txt").write_text("diverged", encoding="utf-8")
        self.git("add", "other.txt")
        self.commit()
        divergent = self.git("rev-parse", "HEAD")
        worker = FakeWorker()
        result = self.run_worker(worker)
        self.assertIs(result.status, RuntimeWorkerStatus.PREPARATION_FAILED)
        self.assertFalse(worker.contexts)
        self.assertEqual(divergent, self.git("rev-parse", "HEAD"))

    def test_new_work_missing_or_invalid_branch_is_not_derived(self):
        for branch in (None, "", "-danger", "@{-1}", "HEAD"):
            with self.subTest(branch=branch):
                worker = FakeWorker()
                result = self.run_worker(worker, new_branch=branch)
                self.assertIs(result.status, RuntimeWorkerStatus.PREPARATION_FAILED)
                self.assertFalse(worker.contexts)
        self.assertEqual("base", self.git("branch", "--show-current"))

    def test_new_work_existing_divergent_branch_is_preserved(self):
        self.git("branch", "caller/chosen")
        (self.repo / "other.txt").write_text("advance", encoding="utf-8")
        self.git("add", "other.txt")
        self.commit()
        self.intended = IntendedRefObservation("base", self.git("rev-parse", "HEAD"))
        result = self.run_worker()
        self.assertIs(result.status, RuntimeWorkerStatus.PREPARATION_FAILED)
        self.assertEqual(self.head, self.git("rev-parse", "caller/chosen"))

    def test_unavailable_exact_commit_or_existing_branch_fails(self):
        self.intended = IntendedRefObservation("base", "b" * 40)
        worker = FakeWorker()
        self.assertIs(self.run_worker(worker).status, RuntimeWorkerStatus.PREPARATION_FAILED)
        self.intended = IntendedRefObservation("base", self.head)
        self.existing = discover_existing_work("CHECKPOINT", (
            ExistingWorkObservation("CHECKPOINT", 42, "missing", self.head, True),
        ))
        self.assertIs(self.run_worker(worker).status, RuntimeWorkerStatus.PREPARATION_FAILED)
        self.assertFalse(worker.contexts)

    def test_worker_exceptions_and_interruptions_never_retry_or_discard(self):
        for error in (RuntimeError("secret"), KeyboardInterrupt(), SystemExit()):
            with self.subTest(error=type(error)):
                def action(context):
                    (context.repository / "allowed.txt").write_text("keep", encoding="utf-8")
                    raise error
                worker = FakeWorker(action)
                # Reuse an already prepared clean branch in subsequent subcases.
                if (self.repo / "allowed.txt").read_text() != "before\n":
                    (self.repo / "allowed.txt").write_text("before\n", encoding="utf-8")
                result = self.run_worker(worker)
                self.assertIs(result.status, RuntimeWorkerStatus.WORKER_UNTRUSTWORTHY)
                self.assertEqual(1, len(worker.contexts))
                self.assertEqual("keep", (self.repo / "allowed.txt").read_text())
                self.assertNotIn("secret", repr(result))

    def test_unreliable_or_invalid_worker_return_is_uncertain(self):
        for value in (None, WorkerCompletion(False), WorkerCompletion(1)):
            with self.subTest(value=value):
                worker = FakeWorker(lambda context: value)
                result = self.run_worker(worker)
                self.assertIs(result.status, RuntimeWorkerStatus.WORKER_UNTRUSTWORTHY)
                self.assertEqual(1, len(worker.contexts))

    def test_worker_head_change_is_rejected_and_preserved(self):
        def action(context):
            self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                     "commit", "--allow-empty", "-m", "violating fake")
            return WorkerCompletion(True)
        result = self.run_worker(FakeWorker(action))
        self.assertIs(result.status, RuntimeWorkerStatus.BOUNDARY_INVALID)
        self.assertNotEqual(self.head, self.git("rev-parse", "HEAD"))

    def test_worker_branch_change_is_rejected(self):
        def action(context):
            self.git("switch", "base")
            return WorkerCompletion(True)
        result = self.run_worker(FakeWorker(action))
        self.assertIs(result.status, RuntimeWorkerStatus.BOUNDARY_INVALID)
        self.assertEqual("base", self.git("branch", "--show-current"))

    def test_worker_staging_allowed_file_is_independently_forbidden(self):
        def action(context):
            (context.repository / "allowed.txt").write_text("staged", encoding="utf-8")
            self.git("add", "allowed.txt")
            return WorkerCompletion(True)
        result = self.run_worker(FakeWorker(action))
        self.assertTrue(result.paths.is_valid)
        self.assertTrue(result.boundary.is_valid)
        self.assertTrue(result.staged_changes_forbidden)
        self.assertIs(result.status, RuntimeWorkerStatus.BOUNDARY_INVALID)
        self.assertEqual("allowed.txt", self.git("diff", "--cached", "--name-only"))

    def test_permitted_unstaged_tracked_change_is_accepted(self):
        result = self.run_worker(self.edit("allowed.txt"))
        self.assertTrue(result.publication_candidate)
        self.assertEqual(("allowed.txt",), result.post_worker.changed_paths.unstaged)

    def test_permitted_untracked_change_is_included(self):
        result = self.run_worker(self.edit("new.txt"))
        self.assertTrue(result.publication_candidate)
        self.assertEqual(("new.txt",), result.paths.actual_paths)
        self.assertEqual(("new.txt",), result.post_worker.changed_paths.untracked)

    def test_unauthorized_tracked_change_is_rejected(self):
        result = self.run_worker(self.edit("other.txt"))
        self.assertIs(result.status, RuntimeWorkerStatus.BOUNDARY_INVALID)
        self.assertEqual(("other.txt",), result.paths.unexpected_paths)

    def test_unauthorized_untracked_change_is_preserved_and_rejected(self):
        result = self.run_worker(self.edit("unauthorized.txt"))
        self.assertIs(result.status, RuntimeWorkerStatus.BOUNDARY_INVALID)
        self.assertEqual(("unauthorized.txt",), result.paths.unexpected_paths)
        self.assertTrue((self.repo / "unauthorized.txt").exists())

    def test_allowlist_has_no_glob_prefix_normalization_or_case_folding(self):
        for permitted in (("*.txt",), ("allowed",), ("./allowed.txt",), ("ALLOWED.txt",)):
            with self.subTest(permitted=permitted):
                (self.repo / "allowed.txt").write_text("before\n", encoding="utf-8")
                result = self.run_worker(self.edit("allowed.txt"), permitted_paths=permitted)
                self.assertIs(result.status, RuntimeWorkerStatus.BOUNDARY_INVALID)

    def test_checks_are_grouped_focused_then_publication_in_declaration_order(self):
        checks = (self.check("pub", CheckKind.PUBLICATION),
                  self.check("f2"), self.check("f1"))
        calls = []
        def process(argv, **kwargs):
            calls.append((argv, kwargs["cwd"]))
            self.assertTrue(argv[0] == "git")
            return run_native_process(argv, **kwargs)
        checker = FakeCheckExecutor()
        with patch("codex_autonomy_runner.runtime_worker.run_native_process", side_effect=process):
            result = self.run_worker(checks=checks, check_executor=checker)
        self.assertTrue(result.publication_candidate)
        self.assertEqual(("f2", "f1"), tuple(e.check_id for e in result.focused_checks))
        self.assertEqual(("pub",), tuple(e.check_id for e in result.publication_checks))
        self.assertFalse(any(argv[0] == sys.executable for argv, cwd in calls))
        executed = [(ctx.argv, ctx.repository) for ctx in checker.contexts]
        self.assertEqual([(checks[1].argv, self.repo.resolve()),
                          (checks[2].argv, self.repo.resolve()),
                          (checks[0].argv, self.repo.resolve())], executed)

    def test_no_undeclared_checks_or_publication_commands(self):
        calls = []
        def process(argv, **kwargs):
            calls.append(argv)
            return run_native_process(argv, **kwargs)
        with patch("codex_autonomy_runner.runtime_worker.run_native_process", side_effect=process):
            result = self.run_worker(self.edit("new.txt"))
        self.assertEqual((), result.focused_checks)
        self.assertEqual((), result.publication_checks)
        self.assertTrue(all(call[0] == "git" for call in calls))
        self.assertTrue(all(call[1] in ("check-ref-format", "rev-parse", "for-each-ref", "switch")
                            for call in calls))
        self.assertFalse(any(flag in call for call in calls for flag in ("--force", "-C", "reset")))

    def test_focused_failure_skips_publication_checks(self):
        checker = FakeCheckExecutor(lambda context: CheckCompletion(4))
        result = self.run_worker(checks=(
            self.check("fail", code="raise SystemExit(4)"),
            self.check("pub", CheckKind.PUBLICATION),
        ), check_executor=checker)
        self.assertEqual(1, len(checker.contexts))
        self.assertIs(result.status, RuntimeWorkerStatus.FOCUSED_UNSATISFIED)
        self.assertFalse(result.publication_candidate)
        self.assertEqual(4, result.focused_checks[0].returncode)
        self.assertEqual((), result.publication_checks)

    def test_publication_failure_is_distinct(self):
        result = self.run_worker(checks=(self.check("focused"), self.check(
            "pub", CheckKind.PUBLICATION, "raise SystemExit(5)"),),
            check_executor=FakeCheckExecutor(lambda ctx: CheckCompletion(
                5 if ctx.kind is CheckKind.PUBLICATION else 0)))
        self.assertIs(result.status, RuntimeWorkerStatus.PUBLICATION_UNSATISFIED)
        self.assertTrue(result.focused_checks[0].satisfied)
        self.assertFalse(result.publication_checks[0].satisfied)

    def test_check_launch_failure_is_runtime_failure_not_blocked(self):
        check = CheckDeclaration("missing", CheckKind.FOCUSED, ("missing-executable",))
        checker = FakeCheckExecutor(lambda context: CheckCompletion(technical_failure=True))
        result = self.run_worker(checks=(check,), check_executor=checker)
        self.assertIs(result.status, RuntimeWorkerStatus.CHECK_RUNTIME_FAILURE)
        self.assertTrue(result.focused_checks[0].technical_failure)
        self.assertIs(result.invocation_result.outcome, InvocationOutcome.RUNTIME_EXECUTION_FAILURE)
        self.assertNotIn("secret", repr(result))

    def test_unexpected_post_worker_inspection_failure_is_uncertain(self):
        initial = inspect_repository(self.repo)
        prepared = replace(initial, branch="caller/chosen")
        worker = FakeWorker()
        with patch("codex_autonomy_runner.runtime_worker.inspect_repository",
                   side_effect=(initial, prepared, RuntimeError("unknown"))):
            result = self.run_worker(worker)
        self.assertIs(result.status, RuntimeWorkerStatus.WORKER_UNTRUSTWORTHY)
        self.assertEqual(1, len(worker.contexts))

    def test_unexpected_check_failure_is_uncertain_and_not_retried(self):
        check = self.check("uncertain")
        def action(context):
            raise RuntimeError("completion unknown")
        checker = FakeCheckExecutor(action)
        result = self.run_worker(checks=(check,), check_executor=checker)
        self.assertIs(result.status, RuntimeWorkerStatus.CHECK_UNTRUSTWORTHY)
        self.assertIs(result.invocation_result.outcome, InvocationOutcome.INTERRUPTED_OR_UNTRUSTWORTHY)
        self.assertEqual(1, len(checker.contexts))
        self.assertNotIn("completion unknown", repr(result))

    def test_profile_and_instructions_are_opaque_hidden_and_not_evidence(self):
        profile = {"secret": "provider-private"}
        instructions = object()
        worker = FakeWorker()
        result = self.run_worker(worker, execution_profile=profile, instructions=instructions)
        self.assertIs(profile, worker.contexts[0].execution_profile)
        self.assertIs(instructions, worker.contexts[0].instructions)
        self.assertNotIn("provider-private", repr(result))
        self.assertNotIn("provider-private", repr(worker.contexts[0]))
        self.assertEqual("attempt-1", result.attempt_id)
        self.assertEqual({"repository", "checkpoint_id", "expected_head_sha", "permitted_paths",
                          "attempt_id", "instructions", "execution_profile"},
                         {f.name for f in fields(WorkerContext)})

    def test_check_output_and_argv_are_excluded_from_evidence(self):
        check = self.check("output", code="print('private-output')")
        result = self.run_worker(checks=(check,))
        self.assertNotIn("private-output", repr(result))
        self.assertNotIn("private-output", repr(check))
        self.assertEqual({"check_id", "kind", "returncode", "technical_failure"},
                         {f.name for f in fields(result.focused_checks[0])})

    def test_declarations_reject_shell_strings_and_duplicate_ids(self):
        with self.assertRaises(ValueError):
            CheckDeclaration("bad", CheckKind.FOCUSED, "echo unsafe")
        worker = FakeWorker()
        result = self.run_worker(worker, checks=(self.check("same"), self.check("same")))
        self.assertIs(result.status, RuntimeWorkerStatus.PREPARATION_FAILED)
        self.assertFalse(worker.contexts)

    def test_results_are_immutable_and_deterministic(self):
        first = self.run_worker()
        second = self.run_worker()
        self.assertEqual(first, second)
        with self.assertRaises(FrozenInstanceError):
            first.status = RuntimeWorkerStatus.PREPARATION_FAILED
        self.assertIsNone(first.invocation_result)
        self.assertTrue(first.publication_candidate)

    def test_check_creating_unauthorized_file_invalidates_boundary(self):
        def action(context):
            (context.repository / "oops").write_text("x", encoding="utf-8")
            return CheckCompletion(0)
        result = self.run_worker(checks=(self.check(
            "mutating-check"),), check_executor=FakeCheckExecutor(action))
        self.assertIs(result.status, RuntimeWorkerStatus.BOUNDARY_INVALID)
        self.assertFalse(result.publication_candidate)

    def test_missing_check_executor_has_no_native_fallback(self):
        worker = FakeWorker()
        with patch("codex_autonomy_runner.runtime_worker.run_native_process") as native:
            result = self.run_worker(worker, checks=(self.check("check"),), check_executor=None)
        native.assert_not_called()
        self.assertFalse(worker.contexts)
        self.assertIs(result.status, RuntimeWorkerStatus.CHECK_RUNTIME_FAILURE)

    def test_uncertain_interrupted_and_invalid_check_completions_fail_closed(self):
        cases = (CheckCompletion(completion_reliable=False), None, CheckCompletion(True),
                 CheckCompletion(0, technical_failure=True), KeyboardInterrupt(), SystemExit())
        for value in cases:
            with self.subTest(value=value):
                def action(context):
                    if isinstance(value, BaseException):
                        raise value
                    return value
                checker = FakeCheckExecutor(action)
                result = self.run_worker(checks=(self.check("f"), self.check("p", CheckKind.PUBLICATION)),
                                         check_executor=checker)
                self.assertIs(result.status, RuntimeWorkerStatus.CHECK_UNTRUSTWORTHY)
                self.assertFalse(result.publication_candidate)
                self.assertEqual(1, len(checker.contexts))
                self.assertEqual((), result.publication_checks)
                self.assertIs(result.invocation_result.outcome, InvocationOutcome.INTERRUPTED_OR_UNTRUSTWORTHY)

    def test_check_context_is_immutable_redacted_and_has_no_authority_helpers(self):
        checker = FakeCheckExecutor()
        check = CheckDeclaration("opaque", CheckKind.FOCUSED, ("any-interpreter", "private-argv"))
        result = self.run_worker(checks=(check,), check_executor=checker)
        context = checker.contexts[0]
        self.assertEqual({"repository", "check_id", "kind", "argv", "attempt_id"},
                         {item.name for item in fields(context)})
        self.assertNotIn("private-argv", repr(context))
        self.assertNotIn("private-argv", repr(result))
        self.assertEqual({"returncode", "technical_failure", "completion_reliable"},
                         {item.name for item in fields(CheckCompletion)})
        with self.assertRaises(FrozenInstanceError):
            context.attempt_id = "changed"
        self.assertIn("not an OS sandbox", CheckExecutor.__doc__)
        self.assertIn("Deny network", CheckExecutor.__doc__)
        self.assertIn("Git metadata/.git writes", CheckExecutor.__doc__)

    def test_checks_cannot_reach_native_launcher_even_with_arbitrary_argv(self):
        declarations = tuple(CheckDeclaration(str(i), CheckKind.FOCUSED, argv) for i, argv in enumerate((
            ("git", "push"), ("gh", "api"), ("python", "remote_script.py"))))
        checker = FakeCheckExecutor(lambda context: CheckCompletion(technical_failure=True))
        before = inspect_repository(self.repo)
        prepared = replace(before, branch="caller/chosen")
        from codex_autonomy_runner.pre_worker_preparation import PreWorkerPreparationValidation
        with patch("codex_autonomy_runner.runtime_worker._prepare",
                   return_value=(prepared, PreWorkerPreparationValidation(()))), patch(
                       "codex_autonomy_runner.runtime_worker.inspect_repository", return_value=prepared), patch(
                       "codex_autonomy_runner.runtime_worker.run_native_process") as native:
            for declaration in declarations:
                self.run_worker(checks=(declaration,), check_executor=checker)
        native.assert_not_called()
        self.assertEqual([d.argv for d in declarations], [c.argv for c in checker.contexts])

    def test_check_tracked_change_is_rejected(self):
        def action(context):
            (context.repository / "other.txt").write_text("unexpected", encoding="utf-8")
            return CheckCompletion(0)
        self.assert_check_boundary_rejected(action)

    def test_check_staging_allowed_file_is_rejected(self):
        def action(context):
            (context.repository / "allowed.txt").write_text("staged", encoding="utf-8")
            self.git("add", "allowed.txt")
            return CheckCompletion(0)
        result = self.assert_check_boundary_rejected(action)
        self.assertTrue(result.staged_changes_forbidden)

    def test_check_head_change_is_rejected(self):
        def action(context):
            self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                     "commit", "--allow-empty", "-m", "violating fake check")
            return CheckCompletion(0)
        self.assert_check_boundary_rejected(action)

    def test_check_branch_change_is_rejected(self):
        def action(context):
            self.git("switch", "base")
            return CheckCompletion(0)
        self.assert_check_boundary_rejected(action)

    def test_check_detached_head_is_rejected(self):
        def action(context):
            self.git("switch", "--detach", self.head)
            return CheckCompletion(0)
        self.assert_check_boundary_rejected(action)

    def assert_check_boundary_rejected(self, action):
        checker = FakeCheckExecutor(action)
        result = self.run_worker(checks=(self.check("check"),), check_executor=checker)
        self.assertIs(result.status, RuntimeWorkerStatus.BOUNDARY_INVALID)
        self.assertFalse(result.publication_candidate)
        self.assertEqual(1, len(checker.contexts))
        return result


if __name__ == "__main__":
    unittest.main()
