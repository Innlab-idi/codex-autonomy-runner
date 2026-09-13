"""Narrow durable-observation and FINALIZER pre-pass integration.

This module accepts an already-selected checkpoint and the policy facts that
cannot be mechanically inferred. It never parses a queue, selects work, calls
a worker, or creates/updates an ordinary pull request.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import json
import re
from typing import Optional, Protocol, Tuple

from .existing_work import ExistingWorkObservation, discover_existing_work
from .finalizer_closure import ALLOWLISTED_CLOSURE_PATH, derive_allowlisted_closure, validate_allowlisted_closure
from .finalizer_eligibility import (
    CurrentHeadRelationship, FinalizerEligibilityObservation, FinalizerTarget,
    FinalizerEligibility, EligibleFinalizerUnit, FinalizerEligibilityPath, validate_finalizer_eligibility,
)
from .finalizer_merge import (
    FinalizerMergeStatus, FinalizerMergeViolation, ProtectedMergeRequest,
    ProtectedMergeTransport, ProtectedMergeTransportResult,
    merge_after_fresh_revalidation,
)
from .finalizer_publication import (
    FinalizerPublicationStatus, FinalizerPublicationTransport, publish_allowlisted_closure,
)
from .invocation_contract import InvocationOutcome, InvocationResult
from .native_process import NativeProcessLaunchError, run_native_process
from .repository_inspection import ChangedPaths, RepositoryInspection, inspect_repository
from .runtime_invocation import repository_identity
from .supervisor_decisions import (
    SupervisorDecisionKind, SupervisorDecisionObservation, discover_supervisor_decisions,
)


_DECISION_MARKERS = {
    "AI_SUPERVISOR: APPROVED": SupervisorDecisionKind.APPROVED,
    "AI_SUPERVISOR: AI_REWORK": SupervisorDecisionKind.AI_REWORK,
    "AI_SUPERVISOR: HUMAN_REQUIRED": SupervisorDecisionKind.HUMAN_REQUIRED,
}
_HEAD_BINDING_PREFIXES = (
    "Reviewed substantive HEAD ",
    "Reviewed exact HEAD ",
    "This approval is bound only to substantive HEAD ",
    "This approval is bound only to HEAD ",
)
_HEAD_BINDINGS = (
    re.compile(r"Reviewed substantive HEAD `([0-9a-f]{40})` for .+\."),
    re.compile(r"Reviewed exact HEAD `([0-9a-f]{40})` for .+\."),
    re.compile(
        r"This approval is bound only to substantive HEAD `([0-9a-f]{40})`\. "
        r"Any later substantive change requires fresh review\."
    ),
    re.compile(
        r"This approval is bound only to HEAD `([0-9a-f]{40})`\. "
        r"Any later change requires fresh review\."
    ),
)
_CREATED_AT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
_SHA = re.compile(r"[0-9a-f]{40}")


def _valid_created_at(value: object) -> bool:
    if not isinstance(value, str) or _CREATED_AT.fullmatch(value) is None:
        return False
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class DirectedFinalizerContext:
    """Identity and policy facts supplied by the already-authorized caller."""

    repository: object
    checkpoint_id: str
    pr_number: int
    checkpoint_gate: str
    checkpoint_state: str
    substantive_head_sha: str
    checks_satisfied: bool
    has_human_reserved_condition: bool
    is_non_destructive: bool
    source_work_queue: str
    existing_work_pr_numbers: Tuple[int, ...] = ()


@dataclass(frozen=True)
class DurablePullRequest:
    """The mutable PR facts this layer may obtain without interpreting prose."""

    number: int
    branch: str
    head_sha: str
    is_active: bool
    is_mergeable: bool


@dataclass(frozen=True)
class DurableComment:
    """Top-level durable comment data; text is parsed only by ``parse_decision``."""

    comment_id: int
    body: str = field(repr=False)
    created_at: str


class DurableStateTransport(Protocol):
    """Read exactly one pre-directed PR and its top-level decision comments."""

    def observe_pull_request(self, pr_number: int) -> DurablePullRequest: ...
    def observe_comments(self, pr_number: int) -> Tuple[DurableComment, ...]: ...
    def observe_existing_work(
        self, checkpoint_id: str, candidate_pr_numbers: Tuple[int, ...]
    ) -> Tuple[ExistingWorkObservation, ...]: ...


class FinalizerPrepassStatus(Enum):
    NO_FINALIZATION = "no_finalization"
    CLOSURE_PUBLISHED = "closure_published"
    MERGED = "merged"
    RUNTIME_FAILURE = "runtime_failure"
    INTERRUPTED_OR_UNTRUSTWORTHY = "interrupted_or_untrustworthy"


@dataclass(frozen=True)
class FinalizerPrepassResult:
    status: FinalizerPrepassStatus
    invocation_result: InvocationResult
    refresh_required: bool
    eligibility: object


def parse_decision(comment: DurableComment, checkpoint_id: str, pr_number: int) -> Optional[SupervisorDecisionObservation]:
    """Parse a marker plus one unambiguous bounded HEAD-binding grammar."""

    lines = comment.body.splitlines()
    if not lines or lines[0] not in _DECISION_MARKERS:
        return None
    if any(line in _DECISION_MARKERS for line in lines[1:]):
        return None
    candidates = []
    for line in lines[1:]:
        if not line.startswith(_HEAD_BINDING_PREFIXES):
            continue
        matches = tuple(
            match.group(1)
            for pattern in _HEAD_BINDINGS
            for match in (pattern.fullmatch(line),)
            if match is not None
        )
        if len(matches) != 1:
            return None
        candidates.extend(matches)
    if len(set(candidates)) != 1:
        return None
    return SupervisorDecisionObservation(
        checkpoint_id, pr_number, candidates[0], _DECISION_MARKERS[lines[0]], False
    )


def _reconcile_decisions(
    comments: Tuple[DurableComment, ...], checkpoint_id: str, pr_number: int
):
    """Assign current status strictly from durable comment chronology."""

    if any(
        type(comment.comment_id) is not int
        or comment.comment_id <= 0
        or not isinstance(comment.body, str)
        or not _valid_created_at(comment.created_at)
        for comment in comments
    ):
        raise RuntimeError("Malformed durable comment observation")
    parsed = tuple(
        (comment, observation)
        for comment in comments
        for observation in (parse_decision(comment, checkpoint_id, pr_number),)
        if observation is not None
    )
    observations = []
    by_head = {}
    for comment, observation in parsed:
        by_head.setdefault(observation.reviewed_head_sha, []).append((comment, observation))
    for group in by_head.values():
        comment_ids = [comment.comment_id for comment, _ in group]
        order_keys = [(comment.created_at, comment.comment_id) for comment, _ in group]
        ambiguous = len(set(comment_ids)) != len(comment_ids) or len(set(order_keys)) != len(order_keys)
        latest = None if ambiguous else max(order_keys)
        for comment, observation in group:
            observations.append(
                SupervisorDecisionObservation(
                    observation.checkpoint_id,
                    observation.pr_number,
                    observation.reviewed_head_sha,
                    observation.decision,
                    ambiguous or (comment.created_at, comment.comment_id) == latest,
                )
            )
    return tuple(observations), parsed


def adapt_durable_state(
    context: DirectedFinalizerContext, transport: DurableStateTransport
):
    """Adapt fresh mutable facts to CORE-04/05; checkpoint identity is input."""

    pull_request = transport.observe_pull_request(context.pr_number)
    if pull_request.number != context.pr_number:
        raise RuntimeError("Directed pull request identity changed")
    comments = transport.observe_comments(context.pr_number)
    work_observations = transport.observe_existing_work(
        context.checkpoint_id, context.existing_work_pr_numbers
    )
    existing = discover_existing_work(context.checkpoint_id, work_observations)
    observations, parsed = _reconcile_decisions(
        comments, context.checkpoint_id, context.pr_number
    )
    decisions = discover_supervisor_decisions(
        context.checkpoint_id, context.pr_number, context.substantive_head_sha, observations
    )
    applicable = sorted(
        ((comment, observation) for comment, observation in parsed
         if observation.reviewed_head_sha == context.substantive_head_sha),
        key=lambda item: (item[0].created_at, item[0].comment_id),
    )
    current = decisions.unique_observation
    later_invalidating = bool(
        current is not None
        and current.decision in (
            SupervisorDecisionKind.AI_REWORK,
            SupervisorDecisionKind.HUMAN_REQUIRED,
        )
        and any(
            observation.decision is SupervisorDecisionKind.APPROVED
            for _, observation in applicable[:-1]
        )
    )
    return pull_request, existing, decisions, later_invalidating


def _valid_closure(context: DirectedFinalizerContext, inspection: RepositoryInspection,
                   pull_request: DurablePullRequest) -> bool:
    """Prove a later head is precisely FINALIZER-02's allowed closure."""

    if pull_request.head_sha == context.substantive_head_sha:
        return False
    unit = EligibleFinalizerUnit(repository_identity(inspection), context.checkpoint_id,
        context.pr_number, pull_request.branch, context.substantive_head_sha,
        context.substantive_head_sha)
    derived = derive_allowlisted_closure(
        FinalizerEligibility(FinalizerEligibilityPath.CLOSURE_REQUIRED, (), unit),
        context.source_work_queue,
    )
    if not derived.is_valid or derived.plan is None:
        return False
    candidate = run_native_process(("git", "show", "{}:{}".format(
        pull_request.head_sha, ALLOWLISTED_CLOSURE_PATH)), cwd=inspection.root)
    changed = run_native_process(("git", "diff", "--no-renames", "--name-only", "-z",
        context.substantive_head_sha, pull_request.head_sha), cwd=inspection.root)
    if candidate.returncode != 0 or changed.returncode != 0:
        return False
    paths = tuple(sorted(path for path in changed.stdout.split("\0") if path))
    return validate_allowlisted_closure(
        derived.plan, candidate.stdout, ChangedPaths((), paths, ())
    ).is_valid


def build_eligibility_observation(
    context: DirectedFinalizerContext, transport: DurableStateTransport
):
    """Build FINALIZER-01 input without changing any FINALIZER policy."""

    inspection = inspect_repository(context.repository)
    if repository_identity(inspection) != repository_identity(inspect_repository(context.repository)):
        raise RuntimeError("Repository identity moved")
    pull_request, existing, decisions, invalidated = adapt_durable_state(context, transport)
    is_valid_closure = _valid_closure(context, inspection, pull_request)
    relationship = (CurrentHeadRelationship.VALID_ALLOWLISTED_CLOSURE
                    if is_valid_closure else CurrentHeadRelationship.SUBSTANTIVE_HEAD)
    target = FinalizerTarget(repository_identity(inspection), context.checkpoint_id,
                             context.pr_number, context.substantive_head_sha)
    observation = FinalizerEligibilityObservation(
        repository_id=target.repository_id, checkpoint_id=context.checkpoint_id,
        pr_number=context.pr_number, checkpoint_gate=context.checkpoint_gate,
        checkpoint_state=context.checkpoint_state, current_head_sha=pull_request.head_sha,
        current_head_relationship=relationship, existing_work=existing,
        supervisor_decisions=decisions, has_later_invalidating_decision=invalidated,
        has_later_substantive_change=(pull_request.head_sha != context.substantive_head_sha
                                      and not is_valid_closure),
        checks_satisfied=context.checks_satisfied, is_mergeable=pull_request.is_mergeable,
        has_human_reserved_condition=context.has_human_reserved_condition,
        is_non_destructive=context.is_non_destructive,
    )
    return target, observation


def run_finalizer_prepass(
    context: DirectedFinalizerContext,
    transport: DurableStateTransport,
    *,
    publication_transport: Optional[FinalizerPublicationTransport] = None,
    merge_transport: Optional[ProtectedMergeTransport] = None,
) -> FinalizerPrepassResult:
    """Run one pre-pass. Successful durable mutation always requires refresh."""

    try:
        target, observation = build_eligibility_observation(context, transport)
        eligibility = validate_finalizer_eligibility(target, observation)
    except (NativeProcessLaunchError, OSError, RuntimeError, ValueError):
        return FinalizerPrepassResult(FinalizerPrepassStatus.RUNTIME_FAILURE,
            InvocationResult(InvocationOutcome.RUNTIME_EXECUTION_FAILURE), False, None)

    if eligibility.path is FinalizerEligibilityPath.INELIGIBLE:
        return FinalizerPrepassResult(FinalizerPrepassStatus.NO_FINALIZATION,
            InvocationResult(InvocationOutcome.NO_OP), False, eligibility)
    if eligibility.path is FinalizerEligibilityPath.CLOSURE_REQUIRED:
        if publication_transport is None:
            return FinalizerPrepassResult(FinalizerPrepassStatus.RUNTIME_FAILURE,
                InvocationResult(InvocationOutcome.RUNTIME_EXECUTION_FAILURE), False, eligibility)
        plan = derive_allowlisted_closure(eligibility, context.source_work_queue)
        if not plan.is_valid or plan.plan is None:
            return FinalizerPrepassResult(FinalizerPrepassStatus.NO_FINALIZATION,
                InvocationResult(InvocationOutcome.NO_OP), False, eligibility)
        published = publish_allowlisted_closure(eligibility, plan.plan, publication_transport)
        if published.status is FinalizerPublicationStatus.COMPLETED:
            return FinalizerPrepassResult(FinalizerPrepassStatus.CLOSURE_PUBLISHED,
                InvocationResult(InvocationOutcome.COMPLETED_AUTHORIZED_TRANSITION), True, eligibility)
        status = (FinalizerPrepassStatus.INTERRUPTED_OR_UNTRUSTWORTHY
                  if published.local_closure_head_sha else FinalizerPrepassStatus.RUNTIME_FAILURE)
        outcome = (InvocationOutcome.INTERRUPTED_OR_UNTRUSTWORTHY
                   if published.local_closure_head_sha else InvocationOutcome.RUNTIME_EXECUTION_FAILURE)
        return FinalizerPrepassResult(status, InvocationResult(outcome), False, eligibility)
    if merge_transport is None:
        return FinalizerPrepassResult(FinalizerPrepassStatus.RUNTIME_FAILURE,
            InvocationResult(InvocationOutcome.RUNTIME_EXECUTION_FAILURE), False, eligibility)
    merged = merge_after_fresh_revalidation(eligibility, merge_transport)
    if merged.status is FinalizerMergeStatus.COMPLETED:
        return FinalizerPrepassResult(FinalizerPrepassStatus.MERGED,
            InvocationResult(InvocationOutcome.COMPLETED_AUTHORIZED_TRANSITION), True, eligibility)
    if merged.status is FinalizerMergeStatus.REJECTED:
        if FinalizerMergeViolation.TRANSPORT_SUCCESS_INCONSISTENT in merged.violations:
            return FinalizerPrepassResult(
                FinalizerPrepassStatus.INTERRUPTED_OR_UNTRUSTWORTHY,
                InvocationResult(InvocationOutcome.INTERRUPTED_OR_UNTRUSTWORTHY),
                False,
                eligibility,
            )
        return FinalizerPrepassResult(FinalizerPrepassStatus.NO_FINALIZATION,
            InvocationResult(InvocationOutcome.NO_OP), False, eligibility)
    return FinalizerPrepassResult(FinalizerPrepassStatus.INTERRUPTED_OR_UNTRUSTWORTHY,
        InvocationResult(InvocationOutcome.INTERRUPTED_OR_UNTRUSTWORTHY), False, eligibility)


@dataclass(frozen=True)
class GhDurableStateTransport:
    """Endpoint-limited read transport for one repository; never used in tests."""

    repository_slug: str
    repository: object

    def _json(self, argv) -> object:
        result = run_native_process(argv, cwd=self.repository)
        if result.returncode != 0:
            raise RuntimeError("GitHub observation failed")
        try:
            return json.loads(result.stdout)
        except (TypeError, ValueError) as error:
            raise RuntimeError("Malformed GitHub response") from error

    def observe_pull_request(self, pr_number: int) -> DurablePullRequest:
        data = self._json(("gh", "pr", "view", str(pr_number), "--repo", self.repository_slug,
                           "--json", "number,headRefName,headRefOid,state,mergeable"))
        required = {"number", "headRefName", "headRefOid", "state", "mergeable"}
        if not isinstance(data, dict) or not required.issubset(data):
            raise RuntimeError("Malformed pull request response")
        if (
            type(data["number"]) is not int
            or data["number"] != pr_number
            or not isinstance(data["headRefName"], str)
            or not data["headRefName"]
            or not isinstance(data["headRefOid"], str)
            or _SHA.fullmatch(data["headRefOid"]) is None
            or data["state"] not in ("OPEN", "CLOSED", "MERGED")
            or data["mergeable"] not in ("MERGEABLE", "CONFLICTING", "UNKNOWN")
        ):
            raise RuntimeError("Inconsistent pull request response")
        return DurablePullRequest(pr_number, data["headRefName"], data["headRefOid"],
                                  data["state"] == "OPEN", data["mergeable"] == "MERGEABLE")

    def observe_comments(self, pr_number: int) -> Tuple[DurableComment, ...]:
        data = self._json(("gh", "api", "--paginate", "--slurp",
            "repos/{}/issues/{}/comments".format(self.repository_slug, pr_number)))
        if not isinstance(data, list) or any(not isinstance(page, list) for page in data):
            raise RuntimeError("Malformed comments response")
        comments = []
        for page in data:
            for value in page:
                required = {"id", "body", "created_at"}
                if (
                    not isinstance(value, dict)
                    or not required.issubset(value)
                    or type(value["id"]) is not int
                    or not isinstance(value["body"], str)
                    or not _valid_created_at(value["created_at"])
                ):
                    raise RuntimeError("Malformed comment response")
                comments.append(
                    DurableComment(value["id"], value["body"], value["created_at"])
                )
        return tuple(comments)

    def observe_existing_work(
        self, checkpoint_id: str, candidate_pr_numbers: Tuple[int, ...]
    ) -> Tuple[ExistingWorkObservation, ...]:
        observations = []
        for pr_number in candidate_pr_numbers:
            if type(pr_number) is not int or pr_number <= 0:
                raise RuntimeError("Invalid existing-work candidate")
            pull_request = self.observe_pull_request(pr_number)
            observations.append(
                ExistingWorkObservation(
                    checkpoint_id,
                    pull_request.number,
                    pull_request.branch,
                    pull_request.head_sha,
                    pull_request.is_active,
                )
            )
        return tuple(observations)


@dataclass(frozen=True)
class GhProtectedMergeTransport:
    """One protected-merge endpoint; no PR or repository management surface."""

    repository_slug: str
    repository: object
    repository_id: str

    def merge_protected(self, request: ProtectedMergeRequest) -> ProtectedMergeTransportResult:
        if request.repository_id != self.repository_id:
            return ProtectedMergeTransportResult(False, expected_head_satisfied=False)
        # The REST endpoint checks sha atomically and performs an immediate
        # merge. It has no auto-merge or merge-queue enabling behavior.
        try:
            result = run_native_process((
                "gh", "api", "--method", "PUT",
                "repos/{}/pulls/{}/merge".format(self.repository_slug, request.pr_number),
                "--raw-field", "sha=" + request.expected_head_sha,
                "--raw-field", "merge_method=merge",
            ), cwd=self.repository)
        except (OSError, RuntimeError, UnicodeError):
            raise RuntimeError("Protected merge completion unknown") from None
        if result.returncode != 0:
            # Exit status alone cannot prove a refusal before a side effect.
            # Never infer refusal from stderr or retry the mutation.
            raise RuntimeError("Protected merge completion unknown")
        try:
            data = json.loads(result.stdout)
        except (TypeError, ValueError):
            raise RuntimeError("Malformed protected merge response") from None
        if (
            not isinstance(data, dict)
            or set(data) != {"merged", "sha", "message"}
            or data["merged"] is not True
            or not isinstance(data["sha"], str)
            or _SHA.fullmatch(data["sha"]) is None
            or not isinstance(data["message"], str)
        ):
            raise RuntimeError("Unverified protected merge")
        return ProtectedMergeTransportResult(True, data["sha"], True)
