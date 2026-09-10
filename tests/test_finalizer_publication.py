"""Tests for the narrow FINALIZER-03 local closure-publication boundary."""

from dataclasses import replace
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from codex_autonomy_runner.finalizer_closure import derive_allowlisted_closure
from codex_autonomy_runner.finalizer_eligibility import (
    EligibleFinalizerUnit,
    FinalizerEligibility,
    FinalizerEligibilityPath,
    FinalizerEligibilityViolation,
)
from codex_autonomy_runner.finalizer_publication import (
    FinalizerPublicationFailureStage,
    FinalizerPublicationStatus,
    FinalizerPublicationViolation,
    NativeFinalizerPublicationTransport,
    RemoteRefObservation,
    ResolvedFinalizerPublicationDestination,
    publish_allowlisted_closure,
)
from codex_autonomy_runner.native_process import run_native_process


class FinalizerPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.repo = root / "repo"
        self.remote = root / "remote.git"
        self.repo.mkdir()
        self.git("init")
        self.git("config", "user.name", "Test User")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "core.autocrlf", "false")
        self.git("checkout", "-b", "closure-branch")
        self.source = getattr(self, "fixture_source", (
            "# Queue\n\n| Checkpoint | Gate | State | Scope | Dependency / next action |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| FINALIZER-03 | AI | AI_REVIEW | narrow scope | review ready |\n"
        ))
        self.write(self.source)
        self.git("add", "--", "docs/WORK_QUEUE.md")
        self.git("commit", "-m", "substantive")
        self.substantive = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("init", "--bare", str(self.remote))
        self.git("remote", "add", "closure", str(self.remote))
        self.git("push", "-u", "closure", "HEAD:refs/heads/closure-branch")
        self.unit = EligibleFinalizerUnit(
            "repo", "FINALIZER-03", 33, "closure-branch", self.substantive, self.substantive
        )
        self.eligibility = FinalizerEligibility(FinalizerEligibilityPath.CLOSURE_REQUIRED, (), self.unit)
        self.plan = derive_allowlisted_closure(self.eligibility, self.source).plan
        self.transport = NativeFinalizerPublicationTransport(self.repo, "closure")

    def git(self, *args):
        result = run_native_process(("git", *args), cwd=self.repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def write(self, text):
        path = self.repo / "docs" / "WORK_QUEUE.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))

    def remote_head(self):
        return self.git("--git-dir", str(self.remote), "rev-parse", "refs/heads/closure-branch").stdout.strip()

    def test_happy_path_commits_and_non_force_publishes_exact_closure(self):
        result = publish_allowlisted_closure(self.eligibility, self.plan, self.transport)

        self.assertEqual(FinalizerPublicationStatus.COMPLETED, result.status)
        self.assertEqual(result.published_head_sha, result.local_closure_head_sha)
        self.assertNotEqual(self.substantive, result.published_head_sha)
        self.assertEqual(self.substantive, self.git("rev-parse", "HEAD^").stdout.strip())
        self.assertEqual(self.substantive, self.git("show", "-s", "--format=%P", "HEAD").stdout.strip())
        self.assertEqual(("docs/WORK_QUEUE.md",), tuple(self.git("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").stdout.splitlines()))
        self.assertEqual(self.plan.result_work_queue, self.git("show", "HEAD:docs/WORK_QUEUE.md").stdout)
        self.assertEqual("", self.git("status", "--porcelain").stdout)
        self.assertEqual(result.published_head_sha, self.remote_head())

    def test_rejects_all_eligibility_and_plan_inconsistencies_without_mutation(self):
        cases = (
            FinalizerEligibility(FinalizerEligibilityPath.INELIGIBLE, (), None),
            FinalizerEligibility(FinalizerEligibilityPath.PROTECTED_MERGE, (), self.unit),
            FinalizerEligibility(FinalizerEligibilityPath.CLOSURE_REQUIRED, (FinalizerEligibilityViolation.NOT_MERGEABLE,), self.unit),
            FinalizerEligibility(FinalizerEligibilityPath.CLOSURE_REQUIRED, (), None),
        )
        for eligibility in cases:
            with self.subTest(eligibility=eligibility.path):
                result = publish_allowlisted_closure(eligibility, self.plan, self.transport)
                self.assertEqual(FinalizerPublicationStatus.REJECTED, result.status)
                self.assertEqual(self.substantive, self.git("rev-parse", "HEAD").stdout.strip())
        wrong_unit = replace(self.plan, unit=replace(self.unit, branch="other"))
        self.assertEqual(FinalizerPublicationStatus.REJECTED, publish_allowlisted_closure(self.eligibility, wrong_unit, self.transport).status)
        self.assertEqual(self.substantive, self.remote_head())

    def test_local_preflight_mismatches_reject_before_write(self):
        scenarios = (
            ("head", lambda: self.git("commit", "--allow-empty", "-m", "different")),
            ("branch", lambda: self.git("checkout", "-b", "other")),
            ("detached", lambda: self.git("checkout", "--detach")),
            ("staged", lambda: (self.write(self.source + "x"), self.git("add", "--", "docs/WORK_QUEUE.md"))),
            ("unstaged", lambda: self.write(self.source + "x")),
            ("untracked", lambda: (self.repo / "unexpected").write_text("x", encoding="utf-8")),
        )
        for name, mutate in scenarios:
            with self.subTest(name=name):
                self.setUp()
                mutate()
                result = publish_allowlisted_closure(self.eligibility, self.plan, self.transport)
                self.assertEqual(FinalizerPublicationStatus.REJECTED, result.status)
                self.assertEqual(self.substantive, self.remote_head())

    def test_source_blob_difference_including_newline_is_rejected_before_write(self):
        changed_plan = derive_allowlisted_closure(
            self.eligibility, self.source.replace("\n", "\r\n")
        ).plan
        result = publish_allowlisted_closure(self.eligibility, changed_plan, self.transport)
        self.assertEqual(FinalizerPublicationStatus.REJECTED, result.status)
        self.assertIn(FinalizerPublicationViolation.SOURCE_BLOB_MISMATCH, result.violations)
        self.assertEqual(self.substantive, self.git("rev-parse", "HEAD").stdout.strip())

    def test_remote_missing_and_divergent_refs_reject_without_touching_remote(self):
        self.git("--git-dir", str(self.remote), "update-ref", "-d", "refs/heads/closure-branch")
        missing = publish_allowlisted_closure(self.eligibility, self.plan, self.transport)
        self.assertIn(FinalizerPublicationViolation.REMOTE_REF_MISSING, missing.violations)

        self.setUp()
        self.git("commit", "--allow-empty", "-m", "remote ahead")
        self.git("push", "closure", "HEAD:refs/heads/closure-branch")
        self.git("reset", "--hard", self.substantive)
        divergent = publish_allowlisted_closure(self.eligibility, self.plan, self.transport)
        self.assertIn(FinalizerPublicationViolation.REMOTE_REF_MISMATCH, divergent.violations)
        self.assertNotEqual(self.substantive, self.remote_head())

    def test_fake_transport_failures_and_post_verify_do_not_become_success(self):
        class PostVerifyMismatch(NativeFinalizerPublicationTransport):
            def __init__(self, repo, remote):
                super().__init__(repo, remote)
                self.calls = 0
            def remote_ref(self, destination, branch):
                self.calls += 1
                if self.calls > 2:
                    return RemoteRefObservation(("other",))
                return super().remote_ref(destination, branch)

        result = publish_allowlisted_closure(self.eligibility, self.plan, PostVerifyMismatch(self.repo, "closure"))
        self.assertEqual(FinalizerPublicationStatus.OPERATIONAL_FAILURE, result.status)
        self.assertEqual(FinalizerPublicationFailureStage.REMOTE_POST_VERIFY, result.failure_stage)
        self.assertIsNone(result.published_head_sha)
        self.assertIsNotNone(result.local_closure_head_sha)

    def test_native_remote_argv_closes_options_and_rejects_option_like_remote(self):
        from codex_autonomy_runner.native_process import NativeProcessResult

        transport = NativeFinalizerPublicationTransport(self.repo, "closure")
        with patch("codex_autonomy_runner.finalizer_publication._git") as git:
            git.return_value = NativeProcessResult(
                (), 0, self.substantive + "\trefs/heads/closure-branch\n", ""
            )
            destination = transport.resolve_destination()
            self.assertEqual(
                ("remote", "get-url", "--push", "--all", "--", "closure"),
                git.call_args.args[0],
            )
            transport.remote_ref(destination, "closure-branch")
            self.assertEqual(
                ("ls-remote", "--refs", "--", destination.push_url, "refs/heads/closure-branch"),
                git.call_args.args[0],
            )
            transport.push_head_to_branch(destination, "closure-branch")
            self.assertEqual(
                ("push", "--", destination.push_url, "HEAD:refs/heads/closure-branch"),
                git.call_args.args[0],
            )
        with self.assertRaises(RuntimeError):
            NativeFinalizerPublicationTransport(self.repo, "--upload-pack=x").resolve_destination()

    def test_distinct_pushurl_is_pinned_and_multiple_pushurls_reject_before_write(self):
        push_remote = Path(self.temp.name) / "push.git"
        self.git("init", "--bare", str(push_remote))
        self.git("push", str(push_remote), "HEAD:refs/heads/closure-branch")
        self.git("remote", "set-url", "--push", "closure", str(push_remote))
        result = publish_allowlisted_closure(self.eligibility, self.plan, self.transport)
        self.assertEqual(FinalizerPublicationStatus.COMPLETED, result.status)
        self.assertEqual(self.substantive, self.remote_head())
        pushed = self.git("--git-dir", str(push_remote), "rev-parse", "refs/heads/closure-branch").stdout.strip()
        self.assertEqual(result.published_head_sha, pushed)

        self.setUp()
        other = Path(self.temp.name) / "other.git"
        self.git("init", "--bare", str(other))
        self.git("remote", "set-url", "--push", "closure", str(self.remote))
        self.git("remote", "set-url", "--add", "--push", "closure", str(other))
        result = publish_allowlisted_closure(self.eligibility, self.plan, self.transport)
        self.assertIn(FinalizerPublicationViolation.REMOTE_DESTINATION_AMBIGUOUS, result.violations)
        self.assertEqual(self.substantive, self.git("rev-parse", "HEAD").stdout.strip())

    def test_remote_and_post_write_operational_failures_do_not_mutate_further(self):
        class RemoteFails(NativeFinalizerPublicationTransport):
            def resolve_destination(self):
                return super().resolve_destination()
            def remote_ref(self, destination, branch):
                raise OSError("temporary transport failure")

        result = publish_allowlisted_closure(self.eligibility, self.plan, RemoteFails(self.repo, "closure"))
        self.assertEqual(FinalizerPublicationStatus.OPERATIONAL_FAILURE, result.status)
        self.assertEqual(FinalizerPublicationFailureStage.REMOTE_PREFLIGHT, result.failure_stage)
        self.assertEqual(self.substantive, self.git("rev-parse", "HEAD").stdout.strip())

        self.setUp()
        class ReadFails(NativeFinalizerPublicationTransport):
            def read_work_queue(self):
                raise OSError("read failure")
            def stage_work_queue(self):
                raise AssertionError("must not stage")

        result = publish_allowlisted_closure(self.eligibility, self.plan, ReadFails(self.repo, "closure"))
        self.assertEqual(FinalizerPublicationStatus.OPERATIONAL_FAILURE, result.status)
        self.assertEqual(FinalizerPublicationFailureStage.WRITE, result.failure_stage)
        self.assertEqual(self.substantive, self.git("rev-parse", "HEAD").stdout.strip())

        self.setUp()
        class CandidateMismatch(NativeFinalizerPublicationTransport):
            def write_work_queue(self, text):
                super().write_work_queue("unexpected candidate")
            def stage_work_queue(self):
                raise AssertionError("must not stage")

        result = publish_allowlisted_closure(self.eligibility, self.plan, CandidateMismatch(self.repo, "closure"))
        self.assertIn(FinalizerPublicationViolation.POST_WRITE_INVALID, result.violations)
        self.assertEqual(self.substantive, self.git("rev-parse", "HEAD").stdout.strip())

    def test_staged_blob_and_parent_cardinality_anomalies_prevent_push(self):
        class StagedMismatch(NativeFinalizerPublicationTransport):
            def read_blob(self, revision, path):
                if revision == ":":
                    return "changed"
                return super().read_blob(revision, path)
            def commit_closure(self, checkpoint_id):
                raise AssertionError("must not commit")

        result = publish_allowlisted_closure(self.eligibility, self.plan, StagedMismatch(self.repo, "closure"))
        self.assertIn(FinalizerPublicationViolation.STAGED_BLOB_MISMATCH, result.violations)

        self.setUp()
        class MergeLikeCommit(NativeFinalizerPublicationTransport):
            def parents_of_head(self):
                return (self_outer.substantive, "second-parent")
            def push_head_to_branch(self, destination, branch):
                raise AssertionError("must not push")

        self_outer = self
        result = publish_allowlisted_closure(self.eligibility, self.plan, MergeLikeCommit(self.repo, "closure"))
        self.assertIn(FinalizerPublicationViolation.POST_COMMIT_INVALID, result.violations)
        self.assertIsNotNone(result.local_closure_head_sha)

    def test_remote_movement_or_failure_before_push_preserves_local_closure(self):
        class MovesBeforePush(NativeFinalizerPublicationTransport):
            def __init__(self, repo, remote):
                super().__init__(repo, remote)
                self.calls = 0
            def remote_ref(self, destination, branch):
                self.calls += 1
                if self.calls == 2:
                    return RemoteRefObservation(("advanced",))
                return super().remote_ref(destination, branch)
            def push_head_to_branch(self, destination, branch):
                raise AssertionError("must not push")

        result = publish_allowlisted_closure(self.eligibility, self.plan, MovesBeforePush(self.repo, "closure"))
        self.assertEqual(FinalizerPublicationStatus.REJECTED, result.status)
        self.assertIn(FinalizerPublicationViolation.REMOTE_REF_MISMATCH, result.violations)
        self.assertIsNotNone(result.local_closure_head_sha)
        self.assertIsNone(result.published_head_sha)

    def test_push_failure_is_operational_and_non_force(self):
        class FailingPush(NativeFinalizerPublicationTransport):
            def push_head_to_branch(self, destination, branch):
                self.pushed_branch = branch
                return type("Result", (), {"returncode": 1})()

        transport = FailingPush(self.repo, "closure")
        result = publish_allowlisted_closure(self.eligibility, self.plan, transport)
        self.assertEqual(FinalizerPublicationStatus.OPERATIONAL_FAILURE, result.status)
        self.assertEqual(FinalizerPublicationFailureStage.PUSH, result.failure_stage)
        self.assertIsNone(result.published_head_sha)
        self.assertEqual("closure-branch", transport.pushed_branch)
        self.assertEqual(self.substantive, self.remote_head())

    def test_crlf_and_missing_final_newline_are_preserved(self):
        for source in (self.source.replace("\n", "\r\n"), self.source[:-1]):
            with self.subTest(source=repr(source[-2:])):
                self.fixture_source = source
                self.setUp()
                result = publish_allowlisted_closure(self.eligibility, self.plan, self.transport)
                self.assertEqual(FinalizerPublicationStatus.COMPLETED, result.status)
                blob = subprocess.run(
                    ("git", "show", "HEAD:docs/WORK_QUEUE.md"), cwd=self.repo,
                    check=True, stdout=subprocess.PIPE,
                ).stdout.decode("utf-8", errors="strict")
                self.assertEqual(self.plan.result_work_queue, blob)


if __name__ == "__main__":
    unittest.main()
