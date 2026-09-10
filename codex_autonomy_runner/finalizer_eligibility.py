"""Pure finalizer eligibility reconciliation over supplied durable observations."""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

from .existing_work import ExistingWorkDiscovery, ExistingWorkMatch, ExistingWorkObservation
from .supervisor_decisions import (
    SupervisorDecisionDiscovery,
    SupervisorDecisionKind,
    SupervisorDecisionMatch,
)


class CurrentHeadRelationship(Enum):
    """Caller-supplied mechanical classification of the current PR HEAD."""

    SUBSTANTIVE_HEAD = "substantive_head"
    VALID_ALLOWLISTED_CLOSURE = "valid_allowlisted_closure"


class FinalizerEligibilityPath(Enum):
    """The only subsequent paths this validation can expose."""

    INELIGIBLE = "ineligible"
    CLOSURE_REQUIRED = "closure_required"
    PROTECTED_MERGE = "protected_merge"


class FinalizerEligibilityViolation(Enum):
    """Fixed-order mechanical reasons that prevent finalizer eligibility."""

    REPOSITORY_IDENTITY_MISMATCH = "repository_identity_mismatch"
    CHECKPOINT_IDENTITY_MISMATCH = "checkpoint_identity_mismatch"
    PR_IDENTITY_MISMATCH = "pr_identity_mismatch"
    EXISTING_WORK_NONE = "existing_work_none"
    EXISTING_WORK_AMBIGUOUS = "existing_work_ambiguous"
    EXISTING_WORK_INCONSISTENT = "existing_work_inconsistent"
    GATE_NOT_AI = "gate_not_ai"
    CHECKPOINT_STATE_NOT_FINALIZABLE = "checkpoint_state_not_finalizable"
    SUPERVISOR_DECISION_NONE = "supervisor_decision_none"
    SUPERVISOR_DECISION_AMBIGUOUS = "supervisor_decision_ambiguous"
    SUPERVISOR_DECISION_INCONSISTENT = "supervisor_decision_inconsistent"
    SUPERVISOR_DECISION_NOT_APPROVED = "supervisor_decision_not_approved"
    APPROVAL_INVALIDATED = "approval_invalidated"
    LATER_SUBSTANTIVE_CHANGE = "later_substantive_change"
    CHECKS_NOT_SATISFIED = "checks_not_satisfied"
    NOT_MERGEABLE = "not_mergeable"
    HUMAN_RESERVED_CONDITION = "human_reserved_condition"
    NON_DESTRUCTIVE_PRECONDITION_UNSATISFIED = (
        "non_destructive_precondition_unsatisfied"
    )
    CURRENT_HEAD_RELATIONSHIP_INCOMPATIBLE = "current_head_relationship_incompatible"


@dataclass(frozen=True)
class FinalizerTarget:
    """Exact durable identity to which a finalizer attempt is directed."""

    repository_id: str
    checkpoint_id: str
    pr_number: int
    substantive_head_sha: str


@dataclass(frozen=True)
class FinalizerEligibilityObservation:
    """Fresh facts classified by an authorized caller, without performing I/O."""

    repository_id: str
    checkpoint_id: str
    pr_number: int
    checkpoint_gate: str
    checkpoint_state: str
    current_head_sha: str
    current_head_relationship: CurrentHeadRelationship
    existing_work: ExistingWorkDiscovery
    supervisor_decisions: SupervisorDecisionDiscovery
    has_later_invalidating_decision: bool
    has_later_substantive_change: bool
    checks_satisfied: bool
    is_mergeable: bool
    has_human_reserved_condition: bool
    is_non_destructive: bool


@dataclass(frozen=True)
class EligibleFinalizerUnit:
    """Exact identity retained only for an eligible finalizer path."""

    repository_id: str
    checkpoint_id: str
    pr_number: int
    branch: str
    substantive_head_sha: str
    current_head_sha: str


@dataclass(frozen=True)
class FinalizerEligibility:
    """Structured pure result; eligibility never depends on diagnostic prose."""

    path: FinalizerEligibilityPath
    violations: Tuple[FinalizerEligibilityViolation, ...]
    unit: Optional[EligibleFinalizerUnit]

    @property
    def is_eligible(self) -> bool:
        """Whether the result exposes an exact unit for a later finalizer step."""

        return self.path is not FinalizerEligibilityPath.INELIGIBLE

    @property
    def closure_required(self) -> bool:
        """Whether the approved state still needs the narrow operational closure."""

        return self.path is FinalizerEligibilityPath.CLOSURE_REQUIRED

    @property
    def protected_merge_eligible(self) -> bool:
        """Whether a later protected merge step may be considered."""

        return self.path is FinalizerEligibilityPath.PROTECTED_MERGE


def validate_finalizer_eligibility(
    target: FinalizerTarget,
    observation: FinalizerEligibilityObservation,
) -> FinalizerEligibility:
    """Reconcile supplied facts into a fail-closed finalizer eligibility result.

    The function trusts no discovery cardinality claim without validating its
    retained observation. It deliberately does not infer a valid closure from
    a changed SHA: that relationship is an explicit caller-supplied fact.
    """

    violations = []

    if observation.repository_id != target.repository_id:
        violations.append(FinalizerEligibilityViolation.REPOSITORY_IDENTITY_MISMATCH)
    if observation.checkpoint_id != target.checkpoint_id:
        violations.append(FinalizerEligibilityViolation.CHECKPOINT_IDENTITY_MISMATCH)
    if observation.pr_number != target.pr_number:
        violations.append(FinalizerEligibilityViolation.PR_IDENTITY_MISMATCH)

    existing_observation = _validate_existing_work(target, observation, violations)

    if observation.checkpoint_gate != "AI":
        violations.append(FinalizerEligibilityViolation.GATE_NOT_AI)

    if observation.checkpoint_state not in ("AI_REVIEW", "DONE"):
        violations.append(FinalizerEligibilityViolation.CHECKPOINT_STATE_NOT_FINALIZABLE)

    _validate_supervisor_decision(target, observation, violations)

    if observation.has_later_invalidating_decision:
        violations.append(FinalizerEligibilityViolation.APPROVAL_INVALIDATED)
    if observation.has_later_substantive_change:
        violations.append(FinalizerEligibilityViolation.LATER_SUBSTANTIVE_CHANGE)
    if not observation.checks_satisfied:
        violations.append(FinalizerEligibilityViolation.CHECKS_NOT_SATISFIED)
    if not observation.is_mergeable:
        violations.append(FinalizerEligibilityViolation.NOT_MERGEABLE)
    if observation.has_human_reserved_condition:
        violations.append(FinalizerEligibilityViolation.HUMAN_RESERVED_CONDITION)
    if not observation.is_non_destructive:
        violations.append(
            FinalizerEligibilityViolation.NON_DESTRUCTIVE_PRECONDITION_UNSATISFIED
        )

    if not _has_compatible_current_head(target, observation):
        violations.append(
            FinalizerEligibilityViolation.CURRENT_HEAD_RELATIONSHIP_INCOMPATIBLE
        )

    if violations:
        return FinalizerEligibility(
            path=FinalizerEligibilityPath.INELIGIBLE,
            violations=tuple(violations),
            unit=None,
        )

    assert existing_observation is not None
    unit = EligibleFinalizerUnit(
        repository_id=target.repository_id,
        checkpoint_id=target.checkpoint_id,
        pr_number=target.pr_number,
        branch=existing_observation.branch,
        substantive_head_sha=target.substantive_head_sha,
        current_head_sha=observation.current_head_sha,
    )
    path = (
        FinalizerEligibilityPath.CLOSURE_REQUIRED
        if observation.checkpoint_state == "AI_REVIEW"
        else FinalizerEligibilityPath.PROTECTED_MERGE
    )
    return FinalizerEligibility(path=path, violations=(), unit=unit)


def _validate_existing_work(
    target: FinalizerTarget,
    observation: FinalizerEligibilityObservation,
    violations: list[FinalizerEligibilityViolation],
) -> Optional[ExistingWorkObservation]:
    discovery = observation.existing_work
    if discovery.match is ExistingWorkMatch.NONE:
        violations.append(
            FinalizerEligibilityViolation.EXISTING_WORK_NONE
            if not discovery.observations
            else FinalizerEligibilityViolation.EXISTING_WORK_INCONSISTENT
        )
        return None
    if discovery.match is ExistingWorkMatch.AMBIGUOUS:
        violations.append(
            FinalizerEligibilityViolation.EXISTING_WORK_AMBIGUOUS
            if len(discovery.observations) >= 2
            else FinalizerEligibilityViolation.EXISTING_WORK_INCONSISTENT
        )
        return None
    if discovery.match is not ExistingWorkMatch.UNIQUE or len(discovery.observations) != 1:
        violations.append(FinalizerEligibilityViolation.EXISTING_WORK_INCONSISTENT)
        return None

    existing = discovery.observations[0]
    if (
        not existing.is_active
        or existing.checkpoint_id != target.checkpoint_id
        or existing.pr_number != target.pr_number
        or existing.head_sha != observation.current_head_sha
    ):
        violations.append(FinalizerEligibilityViolation.EXISTING_WORK_INCONSISTENT)
        return None
    return existing


def _validate_supervisor_decision(
    target: FinalizerTarget,
    observation: FinalizerEligibilityObservation,
    violations: list[FinalizerEligibilityViolation],
) -> None:
    discovery = observation.supervisor_decisions
    if discovery.match is SupervisorDecisionMatch.NONE:
        violations.append(
            FinalizerEligibilityViolation.SUPERVISOR_DECISION_NONE
            if not discovery.observations
            else FinalizerEligibilityViolation.SUPERVISOR_DECISION_INCONSISTENT
        )
        return
    if discovery.match is SupervisorDecisionMatch.AMBIGUOUS:
        violations.append(
            FinalizerEligibilityViolation.SUPERVISOR_DECISION_AMBIGUOUS
            if len(discovery.observations) >= 2
            else FinalizerEligibilityViolation.SUPERVISOR_DECISION_INCONSISTENT
        )
        return
    if (
        discovery.match is not SupervisorDecisionMatch.UNIQUE
        or len(discovery.observations) != 1
    ):
        violations.append(FinalizerEligibilityViolation.SUPERVISOR_DECISION_INCONSISTENT)
        return

    decision = discovery.observations[0]
    if (
        not decision.is_current
        or decision.checkpoint_id != target.checkpoint_id
        or decision.pr_number != target.pr_number
        or decision.reviewed_head_sha != target.substantive_head_sha
    ):
        violations.append(FinalizerEligibilityViolation.SUPERVISOR_DECISION_INCONSISTENT)
    elif decision.decision is not SupervisorDecisionKind.APPROVED:
        violations.append(FinalizerEligibilityViolation.SUPERVISOR_DECISION_NOT_APPROVED)


def _has_compatible_current_head(
    target: FinalizerTarget, observation: FinalizerEligibilityObservation
) -> bool:
    if observation.checkpoint_state == "AI_REVIEW":
        return (
            observation.current_head_relationship
            is CurrentHeadRelationship.SUBSTANTIVE_HEAD
            and observation.current_head_sha == target.substantive_head_sha
        )
    if observation.checkpoint_state == "DONE":
        if observation.current_head_relationship is CurrentHeadRelationship.SUBSTANTIVE_HEAD:
            return observation.current_head_sha == target.substantive_head_sha
        if (
            observation.current_head_relationship
            is CurrentHeadRelationship.VALID_ALLOWLISTED_CLOSURE
        ):
            return observation.current_head_sha != target.substantive_head_sha
        return False
    return False
