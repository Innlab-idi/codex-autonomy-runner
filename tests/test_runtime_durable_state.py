from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from codex_autonomy_runner.existing_work import (
    ExistingWorkMatch,
    ExistingWorkObservation,
    discover_existing_work,
)
from codex_autonomy_runner.finalizer_closure import derive_allowlisted_closure
from codex_autonomy_runner.finalizer_eligibility import (
    CurrentHeadRelationship,
    EligibleFinalizerUnit,
    FinalizerEligibility,
    FinalizerEligibilityPath,
    validate_finalizer_eligibility,
)
from codex_autonomy_runner.finalizer_merge import ProtectedMergeRequest, ProtectedMergeTransportResult
from codex_autonomy_runner.finalizer_publication import (
    FinalizerPublicationResult,
    FinalizerPublicationStatus,
)
from codex_autonomy_runner.invocation_contract import InvocationOutcome
from codex_autonomy_runner.native_process import run_native_process
from codex_autonomy_runner.repository_inspection import inspect_repository
from codex_autonomy_runner.runtime_durable_state import (
    DirectedFinalizerContext,
    DurableComment,
    DurablePullRequest,
    FinalizerPrepassStatus,
    GhDurableStateTransport,
    GhProtectedMergeTransport,
    adapt_durable_state,
    build_eligibility_observation,
    parse_decision,
    run_finalizer_prepass,
)
from codex_autonomy_runner.runtime_invocation import repository_identity
from codex_autonomy_runner.supervisor_decisions import (
    SupervisorDecisionKind,
    SupervisorDecisionMatch,
)


HEAD = "a" * 40
OTHER_HEAD = "b" * 40


def process_result(returncode=0, stdout=""):
    return type("ProcessResult", (), {"returncode": returncode, "stdout": stdout})()


class FakeDurable:
    def __init__(self, pr=None, comments=(), work=None):
        self.pr = pr or DurablePullRequest(7, "codex/test", HEAD, True, True)
        self.comments = comments
        self.work = work
        self.candidates = None

    def observe_pull_request(self, number):
        if number != 7:
            raise AssertionError("unexpected directed PR")
        return self.pr

    def observe_comments(self, number):
        if number != 7:
            raise AssertionError("unexpected directed PR")
        return self.comments

    def observe_existing_work(self, checkpoint, candidate_pr_numbers):
        self.candidates = candidate_pr_numbers
        if self.work is not None:
            return self.work
        return tuple(
            ExistingWorkObservation(
                checkpoint, number, self.pr.branch, self.pr.head_sha, self.pr.is_active
            )
            for number in candidate_pr_numbers
        )


class Merge:
    def __init__(self, outcome=None, error=None):
        self.outcome = outcome or ProtectedMergeTransportResult(True, "m" * 40, True)
        self.error = error
        self.requests = []

    def merge_protected(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return self.outcome


class RuntimeDurableStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        self.git("init")
        self.git(
            "-c", "user.name=x", "-c", "user.email=x@y.invalid",
            "commit", "--allow-empty", "-m", "initial",
        )
        self.source = "| RUNTIME-02 | AI | AI_REVIEW | scope | pending |\n"
        self.context = DirectedFinalizerContext(
            repository=self.repo,
            checkpoint_id="RUNTIME-02",
            pr_number=7,
            checkpoint_gate="AI",
            checkpoint_state="AI_REVIEW",
            substantive_head_sha=HEAD,
            checks_satisfied=True,
            has_human_reserved_condition=False,
            is_non_destructive=True,
            source_work_queue=self.source,
            existing_work_pr_numbers=(7,),
        )

    def git(self, *arguments):
        result = run_native_process(("git", *arguments), cwd=self.repo)
        self.assertEqual(0, result.returncode, result.stderr)
        return result.stdout.strip()

    def decision(self, kind="APPROVED", head=HEAD, comment_id=10,
                 created_at="2026-01-01T00:00:00Z", exact=False):
        reviewed = (
            "Reviewed exact HEAD `{}` for `RUNTIME-02`." if exact
            else "Reviewed substantive HEAD `{}` for `RUNTIME-02`."
        ).format(head)
        binding = (
            "This approval is bound only to HEAD `{}`. Any later change requires fresh review."
            if exact else
            "This approval is bound only to substantive HEAD `{}`. "
            "Any later substantive change requires fresh review."
        ).format(head)
        return DurableComment(
            comment_id,
            "AI_SUPERVISOR: {}\n\n{}\n\nReview details.\n\n{}".format(
                kind, reviewed, binding
            ),
            created_at,
        )

    def test_real_supervisor_comment_forms_are_bounded_and_head_bound(self):
        for exact in (False, True):
            with self.subTest(exact=exact):
                parsed = parse_decision(self.decision(exact=exact), "RUNTIME-02", 7)
                self.assertEqual(SupervisorDecisionKind.APPROVED, parsed.decision)
                self.assertEqual(HEAD, parsed.reviewed_head_sha)
                self.assertFalse(parsed.is_current)

    def test_decision_parser_rejects_missing_malformed_or_ambiguous_binding(self):
        marker = "AI_SUPERVISOR: APPROVED"
        bodies = (
            marker,
            marker + "\n\nReviewed exact HEAD `abc123` for `RUNTIME-02`.",
            "notes\n" + marker + "\n\nReviewed exact HEAD `{}` for x.".format(HEAD),
            marker + "\nAI_SUPERVISOR: AI_REWORK\nReviewed exact HEAD `{}` for x.".format(HEAD),
            marker + "\nReviewed exact HEAD `{}` for x.\nReviewed substantive HEAD `{}` for x.".format(HEAD, OTHER_HEAD),
        )
        for body in bodies:
            with self.subTest(body=body):
                self.assertIsNone(parse_decision(
                    DurableComment(1, body, "2026-01-01T00:00:00Z"),
                    "RUNTIME-02", 7,
                ))

    def test_random_sha_and_review_prose_never_determine_decision(self):
        body = (
            "AI_SUPERVISOR: APPROVED\n\nA random SHA {} appears in prose, "
            "without a supported binding."
        ).format(HEAD)
        self.assertIsNone(parse_decision(
            DurableComment(1, body, "2026-01-01T00:00:00Z"),
            "RUNTIME-02", 7,
        ))

    def test_approval_only_is_the_current_exact_head_decision(self):
        _, _, decisions, invalidated = adapt_durable_state(
            self.context, FakeDurable(comments=(self.decision(),))
        )
        self.assertIs(SupervisorDecisionMatch.UNIQUE, decisions.match)
        self.assertIs(SupervisorDecisionKind.APPROVED, decisions.unique_decision)
        self.assertFalse(invalidated)

    def test_no_valid_supervisor_comment_produces_core_none(self):
        _, _, decisions, invalidated = adapt_durable_state(
            self.context, FakeDurable(comments=())
        )
        self.assertIs(SupervisorDecisionMatch.NONE, decisions.match)
        self.assertFalse(invalidated)

    def test_historical_rework_then_later_approval_makes_approval_current(self):
        comments = (
            self.decision("AI_REWORK", comment_id=10),
            self.decision("APPROVED", comment_id=11,
                          created_at="2026-01-02T00:00:00Z"),
        )
        _, _, decisions, invalidated = adapt_durable_state(
            self.context, FakeDurable(comments=comments)
        )
        self.assertIs(SupervisorDecisionKind.APPROVED, decisions.unique_decision)
        self.assertFalse(invalidated)

    def test_later_rework_or_human_required_invalidates_earlier_approval(self):
        for kind in ("AI_REWORK", "HUMAN_REQUIRED"):
            with self.subTest(kind=kind):
                comments = (
                    self.decision("APPROVED", comment_id=10),
                    self.decision(kind, comment_id=11,
                                  created_at="2026-01-02T00:00:00Z"),
                )
                _, _, decisions, invalidated = adapt_durable_state(
                    self.context, FakeDurable(comments=comments)
                )
                self.assertIs(SupervisorDecisionKind[kind], decisions.unique_decision)
                self.assertTrue(invalidated)

    def test_other_head_and_malformed_comments_do_not_affect_target(self):
        comments = (
            self.decision("APPROVED", comment_id=10),
            self.decision("AI_REWORK", head=OTHER_HEAD, comment_id=11,
                          created_at="2026-01-02T00:00:00Z"),
            DurableComment(12, "AI_SUPERVISOR: HUMAN_REQUIRED\nno supported binding",
                           "2026-01-03T00:00:00Z"),
        )
        _, _, decisions, invalidated = adapt_durable_state(
            self.context, FakeDurable(comments=comments)
        )
        self.assertIs(SupervisorDecisionKind.APPROVED, decisions.unique_decision)
        self.assertFalse(invalidated)

    def test_duplicate_durable_order_fails_closed_as_ambiguous(self):
        comments = (
            self.decision("APPROVED", comment_id=10),
            self.decision("AI_REWORK", comment_id=10),
        )
        _, _, decisions, _ = adapt_durable_state(
            self.context, FakeDurable(comments=comments)
        )
        self.assertIs(SupervisorDecisionMatch.AMBIGUOUS, decisions.match)

    def test_native_comments_accept_real_extra_fields_and_all_pages(self):
        pages = [
            [{
                "id": 10, "body": self.decision().body,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:01Z",
                "url": "https://api.invalid/comment/10",
                "user": {"login": "reviewer"},
            }],
            [{
                "id": 11, "body": self.decision(exact=True).body,
                "created_at": "2026-01-02T00:00:00Z",
                "html_url": "https://invalid/comment/11",
            }],
        ]
        calls = []

        def process(argv, cwd):
            calls.append(argv)
            return process_result(stdout=json.dumps(pages))

        transport = GhDurableStateTransport("owner/repo", self.repo)
        with patch("codex_autonomy_runner.runtime_durable_state.run_native_process",
                   side_effect=process):
            comments = transport.observe_comments(7)
        self.assertEqual((10, 11), tuple(item.comment_id for item in comments))
        self.assertEqual(("gh", "api", "--paginate", "--slurp"), calls[0][:4])
        self.assertNotIn("reviewer", repr(comments))

    def test_comment_pagination_changes_reconciliation_result(self):
        pages = [
            [{"id": 10, "body": self.decision("AI_REWORK").body,
              "created_at": "2026-01-01T00:00:00Z"}],
            [{"id": 11, "body": self.decision("APPROVED").body,
              "created_at": "2026-01-02T00:00:00Z"}],
        ]
        transport = GhDurableStateTransport("owner/repo", self.repo)
        with patch(
            "codex_autonomy_runner.runtime_durable_state.run_native_process",
            return_value=process_result(stdout=json.dumps(pages)),
        ):
            comments = transport.observe_comments(7)
        _, _, decisions, invalidated = adapt_durable_state(
            self.context, FakeDurable(comments=comments)
        )
        self.assertIs(SupervisorDecisionKind.APPROVED, decisions.unique_decision)
        self.assertFalse(invalidated)

    def test_native_comments_reject_missing_or_malformed_required_fields(self):
        cases = (
            {},
            [[{"id": 1, "body": "x"}]],
            [[{"id": True, "body": "x", "created_at": "2026-01-01T00:00:00Z"}]],
            [[{"id": 1, "body": None, "created_at": "2026-01-01T00:00:00Z"}]],
            [[{"id": 1, "body": "x", "created_at": "yesterday"}]],
        )
        transport = GhDurableStateTransport("owner/repo", self.repo)
        for data in cases:
            with self.subTest(data=data), patch.object(
                GhDurableStateTransport, "_json", return_value=data
            ):
                with self.assertRaises(RuntimeError):
                    transport.observe_comments(7)

    def test_native_pull_request_accepts_realistic_json_and_ignores_extras(self):
        data = {"number": 7, "headRefName": "codex/test", "headRefOid": HEAD,
                "state": "OPEN", "mergeable": "MERGEABLE",
                "url": "https://invalid/pull/7"}
        transport = GhDurableStateTransport("owner/repo", self.repo)
        with patch.object(GhDurableStateTransport, "_json", return_value=data):
            observed = transport.observe_pull_request(7)
        self.assertEqual(DurablePullRequest(7, "codex/test", HEAD, True, True), observed)

    def test_native_pull_request_rejects_missing_and_wrong_typed_fields(self):
        valid = {"number": 7, "headRefName": "codex/test", "headRefOid": HEAD,
                 "state": "OPEN", "mergeable": "MERGEABLE"}
        cases = (
            {key: value for key, value in valid.items() if key != "headRefOid"},
            dict(valid, number="7"), dict(valid, headRefName=None),
            dict(valid, headRefOid="short"), dict(valid, state=1),
            dict(valid, mergeable=False),
        )
        transport = GhDurableStateTransport("owner/repo", self.repo)
        for data in cases:
            with self.subTest(data=data), patch.object(
                GhDurableStateTransport, "_json", return_value=data
            ):
                with self.assertRaises(RuntimeError):
                    transport.observe_pull_request(7)

    def test_directed_pr_identity_is_not_silently_rebound(self):
        transport = FakeDurable(DurablePullRequest(8, "other", HEAD, True, True))
        with self.assertRaises(RuntimeError):
            adapt_durable_state(self.context, transport)

    def test_native_existing_work_preserves_caller_directed_cardinality(self):
        transport = GhDurableStateTransport("owner/repo", self.repo)
        prs = {
            7: DurablePullRequest(7, "one", HEAD, True, True),
            8: DurablePullRequest(8, "two", OTHER_HEAD, True, True),
        }
        with patch.object(
            GhDurableStateTransport,
            "observe_pull_request",
            side_effect=lambda number: prs[number],
        ):
            none = transport.observe_existing_work("RUNTIME-02", ())
            unique = transport.observe_existing_work("RUNTIME-02", (7,))
            ambiguous = transport.observe_existing_work("RUNTIME-02", (7, 8))
        self.assertIs(ExistingWorkMatch.NONE,
                      discover_existing_work("RUNTIME-02", none).match)
        self.assertIs(ExistingWorkMatch.UNIQUE,
                      discover_existing_work("RUNTIME-02", unique).match)
        self.assertIs(ExistingWorkMatch.AMBIGUOUS,
                      discover_existing_work("RUNTIME-02", ambiguous).match)

    def test_adaptation_passes_exact_candidates_and_preserves_core_states(self):
        cases = (
            ((), ExistingWorkMatch.NONE),
            ((ExistingWorkObservation("RUNTIME-02", 7, "one", HEAD, True),),
             ExistingWorkMatch.UNIQUE),
            ((ExistingWorkObservation("RUNTIME-02", 7, "one", HEAD, True),
              ExistingWorkObservation("RUNTIME-02", 8, "two", OTHER_HEAD, True)),
             ExistingWorkMatch.AMBIGUOUS),
        )
        for work, expected in cases:
            with self.subTest(expected=expected):
                fake = FakeDurable(comments=(self.decision(),), work=work)
                pull, existing, decisions, invalidated = adapt_durable_state(
                    self.context, fake
                )
                self.assertEqual((7,), fake.candidates)
                self.assertEqual(7, pull.number)
                self.assertIs(expected, existing.match)
                self.assertIs(SupervisorDecisionKind.APPROVED, decisions.unique_decision)
                self.assertFalse(invalidated)

    def test_eligibility_observation_preserves_supplied_and_acquired_fields(self):
        target, observation = build_eligibility_observation(
            self.context, FakeDurable(comments=(self.decision(),))
        )
        self.assertEqual(self.context.checkpoint_id, observation.checkpoint_id)
        self.assertEqual(self.context.pr_number, observation.pr_number)
        self.assertEqual(self.context.checkpoint_gate, observation.checkpoint_gate)
        self.assertEqual(self.context.checkpoint_state, observation.checkpoint_state)
        self.assertEqual(HEAD, observation.current_head_sha)
        self.assertIs(CurrentHeadRelationship.SUBSTANTIVE_HEAD,
                      observation.current_head_relationship)
        self.assertEqual(target.repository_id, observation.repository_id)
        self.assertTrue(observation.checks_satisfied)
        self.assertTrue(observation.is_mergeable)
        self.assertFalse(observation.has_human_reserved_condition)
        self.assertTrue(observation.is_non_destructive)

    def _commit_closure_candidate(self, valid):
        queue = self.repo / "docs" / "WORK_QUEUE.md"
        queue.parent.mkdir()
        queue.write_text(self.source, encoding="utf-8")
        self.git("add", "docs/WORK_QUEUE.md")
        self.git("-c", "user.name=x", "-c", "user.email=x@y.invalid",
                 "commit", "-m", "substantive")
        substantive = self.git("rev-parse", "HEAD")
        unit = EligibleFinalizerUnit(
            repository_identity(inspect_repository(self.repo)),
            "RUNTIME-02", 7, "master", substantive, substantive,
        )
        eligibility = FinalizerEligibility(
            FinalizerEligibilityPath.CLOSURE_REQUIRED, (), unit
        )
        plan = derive_allowlisted_closure(eligibility, self.source).plan
        candidate = plan.result_work_queue if valid else plan.result_work_queue + "unexpected\n"
        queue.write_text(candidate, encoding="utf-8")
        self.git("add", "docs/WORK_QUEUE.md")
        self.git("-c", "user.name=x", "-c", "user.email=x@y.invalid",
                 "commit", "-m", "closure")
        return substantive, self.git("rev-parse", "HEAD")

    def test_exact_finalizer_02_closure_is_positively_classified(self):
        substantive, closure = self._commit_closure_candidate(True)
        context = replace(self.context, checkpoint_state="DONE",
                          substantive_head_sha=substantive)
        transport = FakeDurable(
            DurablePullRequest(7, "master", closure, True, True),
            (self.decision(head=substantive),),
        )
        target, observation = build_eligibility_observation(context, transport)
        self.assertIs(CurrentHeadRelationship.VALID_ALLOWLISTED_CLOSURE,
                      observation.current_head_relationship)
        self.assertFalse(observation.has_later_substantive_change)
        self.assertIs(FinalizerEligibilityPath.PROTECTED_MERGE,
                      validate_finalizer_eligibility(target, observation).path)

    def test_malformed_or_unavailable_later_head_is_not_a_valid_closure(self):
        substantive, later = self._commit_closure_candidate(False)
        context = replace(self.context, checkpoint_state="DONE",
                          substantive_head_sha=substantive)
        _, observation = build_eligibility_observation(
            context,
            FakeDurable(DurablePullRequest(7, "master", later, True, True),
                        (self.decision(head=substantive),)),
        )
        self.assertIs(CurrentHeadRelationship.SUBSTANTIVE_HEAD,
                      observation.current_head_relationship)
        self.assertTrue(observation.has_later_substantive_change)
        unavailable = replace(self.context, checkpoint_state="DONE")
        _, observation = build_eligibility_observation(
            unavailable,
            FakeDurable(DurablePullRequest(7, "master", OTHER_HEAD, True, True),
                        (self.decision(),)),
        )
        self.assertIs(CurrentHeadRelationship.SUBSTANTIVE_HEAD,
                      observation.current_head_relationship)

    def _eligible(self, path, current=HEAD):
        unit = EligibleFinalizerUnit(
            "repo", "RUNTIME-02", 7, "branch", HEAD, current
        )
        return FinalizerEligibility(path, (), unit)

    def test_closure_success_requires_refresh_and_never_merges_stale_observation(self):
        publication = Mock(return_value=FinalizerPublicationResult(
            FinalizerPublicationStatus.COMPLETED, published_head_sha="c" * 40
        ))
        with patch("codex_autonomy_runner.runtime_durable_state.build_eligibility_observation",
                   return_value=(object(), object())), patch(
            "codex_autonomy_runner.runtime_durable_state.validate_finalizer_eligibility",
            return_value=self._eligible(FinalizerEligibilityPath.CLOSURE_REQUIRED),
        ), patch("codex_autonomy_runner.runtime_durable_state.derive_allowlisted_closure") as derive, patch(
            "codex_autonomy_runner.runtime_durable_state.publish_allowlisted_closure",
            publication,
        ), patch("codex_autonomy_runner.runtime_durable_state.merge_after_fresh_revalidation") as merge:
            derive.return_value = type("Result", (), {"is_valid": True, "plan": object()})()
            result = run_finalizer_prepass(
                self.context, FakeDurable(), publication_transport=object()
            )
        self.assertIs(FinalizerPrepassStatus.CLOSURE_PUBLISHED, result.status)
        self.assertTrue(result.refresh_required)
        merge.assert_not_called()

    def test_publication_failure_and_uncertainty_never_map_to_blocked(self):
        cases = (
            (None, FinalizerPrepassStatus.RUNTIME_FAILURE,
             InvocationOutcome.RUNTIME_EXECUTION_FAILURE),
            ("c" * 40, FinalizerPrepassStatus.INTERRUPTED_OR_UNTRUSTWORTHY,
             InvocationOutcome.INTERRUPTED_OR_UNTRUSTWORTHY),
        )
        for local_head, expected_status, expected_outcome in cases:
            with self.subTest(local_head=local_head), patch(
                "codex_autonomy_runner.runtime_durable_state.build_eligibility_observation",
                return_value=(object(), object()),
            ), patch(
                "codex_autonomy_runner.runtime_durable_state.validate_finalizer_eligibility",
                return_value=self._eligible(FinalizerEligibilityPath.CLOSURE_REQUIRED),
            ), patch(
                "codex_autonomy_runner.runtime_durable_state.derive_allowlisted_closure",
                return_value=type("R", (), {"is_valid": True, "plan": object()})(),
            ), patch(
                "codex_autonomy_runner.runtime_durable_state.publish_allowlisted_closure",
                return_value=FinalizerPublicationResult(
                    FinalizerPublicationStatus.OPERATIONAL_FAILURE,
                    local_closure_head_sha=local_head,
                ),
            ):
                result = run_finalizer_prepass(
                    self.context, FakeDurable(), publication_transport=object()
                )
            self.assertIs(expected_status, result.status)
            self.assertIs(expected_outcome, result.invocation_result.outcome)
            self.assertIsNot(InvocationOutcome.BLOCKED, result.invocation_result.outcome)

    def test_protected_merge_forwards_exact_head_and_success_requires_refresh(self):
        merge = Merge()
        eligible = self._eligible(FinalizerEligibilityPath.PROTECTED_MERGE, "c" * 40)
        with patch("codex_autonomy_runner.runtime_durable_state.build_eligibility_observation",
                   return_value=(object(), object())), patch(
            "codex_autonomy_runner.runtime_durable_state.validate_finalizer_eligibility",
            return_value=eligible,
        ):
            result = run_finalizer_prepass(
                self.context, FakeDurable(), merge_transport=merge
            )
        self.assertIs(FinalizerPrepassStatus.MERGED, result.status)
        self.assertTrue(result.refresh_required)
        self.assertEqual("c" * 40, merge.requests[0].expected_head_sha)

    def test_merge_refusal_and_operational_uncertainty_are_distinct_not_blocked(self):
        cases = (
            (Merge(ProtectedMergeTransportResult(False, expected_head_satisfied=False)),
             FinalizerPrepassStatus.NO_FINALIZATION, InvocationOutcome.NO_OP),
            (Merge(ProtectedMergeTransportResult(True, merge_commit_sha=None)),
             FinalizerPrepassStatus.INTERRUPTED_OR_UNTRUSTWORTHY,
             InvocationOutcome.INTERRUPTED_OR_UNTRUSTWORTHY),
            (Merge(error=RuntimeError("verification unknown")),
             FinalizerPrepassStatus.INTERRUPTED_OR_UNTRUSTWORTHY,
             InvocationOutcome.INTERRUPTED_OR_UNTRUSTWORTHY),
        )
        for merge, status, outcome in cases:
            with self.subTest(status=status), patch(
                "codex_autonomy_runner.runtime_durable_state.build_eligibility_observation",
                return_value=(object(), object()),
            ), patch(
                "codex_autonomy_runner.runtime_durable_state.validate_finalizer_eligibility",
                return_value=self._eligible(FinalizerEligibilityPath.PROTECTED_MERGE),
            ):
                result = run_finalizer_prepass(
                    self.context, FakeDurable(), merge_transport=merge
                )
            self.assertIs(status, result.status)
            self.assertIs(outcome, result.invocation_result.outcome)
            self.assertIsNot(InvocationOutcome.BLOCKED, result.invocation_result.outcome)

    def test_native_protected_rest_merge_verifies_success_without_auto_merge(self):
        calls = []

        def process(argv, cwd):
            calls.append(argv)
            return process_result(stdout=json.dumps({
                "merged": True, "sha": "d" * 40, "message": "Merged",
            }))

        merger = GhProtectedMergeTransport("owner/repo", self.repo, "repo")
        request = ProtectedMergeRequest("repo", 7, OTHER_HEAD)
        with patch("codex_autonomy_runner.runtime_durable_state.run_native_process",
                   side_effect=process):
            result = merger.merge_protected(request)
        self.assertTrue(result.merged)
        self.assertEqual("d" * 40, result.merge_commit_sha)
        self.assertTrue(result.expected_head_satisfied)
        self.assertEqual([(
            "gh", "api", "--method", "PUT", "repos/owner/repo/pulls/7/merge",
            "--raw-field", "sha=" + OTHER_HEAD,
            "--raw-field", "merge_method=merge",
        )], calls)
        self.assertNotIn("--auto", calls[0])
        self.assertNotEqual(("gh", "pr", "merge"), calls[0][:3])

    def test_native_nonzero_merge_is_uncertain_without_retry_or_fallback(self):
        calls = []

        def process(argv, cwd):
            calls.append(argv)
            return process_result(returncode=1)

        merger = GhProtectedMergeTransport("owner/repo", self.repo, "repo")
        request = type("Request", (), {
            "repository_id": "repo", "pr_number": 7, "expected_head_sha": HEAD
        })()
        with patch("codex_autonomy_runner.runtime_durable_state.run_native_process",
                   side_effect=process):
            with self.assertRaises(RuntimeError):
                merger.merge_protected(request)
        self.assertEqual(1, len(calls))
        self.assertIn("sha=" + HEAD, calls[0])

    def test_rest_merge_errors_and_malformed_success_are_untrustworthy(self):
        malformed = (
            "invalid json", "[]", "{}",
            json.dumps({"merged": True, "sha": "d" * 40}),
            json.dumps({"merged": False, "sha": "d" * 40, "message": "refused"}),
            json.dumps({"merged": 1, "sha": "d" * 40, "message": "x"}),
            json.dumps({"merged": "true", "sha": "d" * 40, "message": "x"}),
            json.dumps({"merged": True, "sha": "short", "message": "x"}),
            json.dumps({"merged": True, "sha": "", "message": "x"}),
            json.dumps({"merged": True, "sha": None, "message": "x"}),
            json.dumps({"merged": True, "sha": "D" * 40, "message": "x"}),
            json.dumps({"merged": True, "sha": "d" * 40, "message": None}),
            json.dumps({"merged": True, "sha": "d" * 40, "message": "x", "extra": 1}),
        )
        cases = [process_result(stdout=body) for body in malformed]
        cases.extend((process_result(returncode=1), OSError("network"),
                      RuntimeError("CLI failure"), UnicodeError("decode failure")))
        for outcome in cases:
            with self.subTest(outcome=outcome):
                self._assert_native_merge_uncertain(outcome)

    def _assert_native_merge_uncertain(self, outcome):
        calls = []

        def process(argv, cwd):
            calls.append(argv)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        merger = GhProtectedMergeTransport("owner/repo", self.repo, "repo")
        with patch("codex_autonomy_runner.runtime_durable_state.build_eligibility_observation",
                   return_value=(object(), object())), patch(
            "codex_autonomy_runner.runtime_durable_state.validate_finalizer_eligibility",
            return_value=self._eligible(FinalizerEligibilityPath.PROTECTED_MERGE),
        ), patch("codex_autonomy_runner.runtime_durable_state.run_native_process",
                 side_effect=process):
            result = run_finalizer_prepass(
                self.context, FakeDurable(), merge_transport=merger
            )
        self.assertIs(FinalizerPrepassStatus.INTERRUPTED_OR_UNTRUSTWORTHY, result.status)
        self.assertIs(InvocationOutcome.INTERRUPTED_OR_UNTRUSTWORTHY,
                      result.invocation_result.outcome)
        self.assertIsNot(InvocationOutcome.NO_OP, result.invocation_result.outcome)
        self.assertIsNot(InvocationOutcome.BLOCKED, result.invocation_result.outcome)
        self.assertEqual(1, len(calls))
        self.assertIn("sha=" + HEAD, calls[0])

    def test_native_repository_mismatch_refuses_before_any_mutation(self):
        merger = GhProtectedMergeTransport("owner/repo", self.repo, "repo")
        with patch("codex_autonomy_runner.runtime_durable_state.run_native_process") as process:
            result = merger.merge_protected(ProtectedMergeRequest("other", 7, HEAD))
        self.assertFalse(result.merged)
        process.assert_not_called()

    def test_native_surfaces_are_narrow_and_expose_no_worker_or_generic_pr_api(self):
        durable_methods = {
            name for name in dir(GhDurableStateTransport) if not name.startswith("__")
        }
        merge_methods = {
            name for name in dir(GhProtectedMergeTransport) if not name.startswith("__")
        }
        self.assertEqual(
            {"_json", "observe_comments", "observe_existing_work", "observe_pull_request"},
            durable_methods,
        )
        self.assertEqual({"merge_protected"}, merge_methods)
        self.assertFalse(any(
            "worker" in name or "create" in name or "update" in name
            for name in durable_methods | merge_methods
        ))


if __name__ == "__main__":
    unittest.main()
