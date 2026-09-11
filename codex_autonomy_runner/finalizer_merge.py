"""Narrow protected-merge boundary for a freshly revalidated finalizer unit."""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Protocol, Tuple

from .finalizer_eligibility import FinalizerEligibility, FinalizerEligibilityPath


class FinalizerMergeStatus(Enum):
    COMPLETED = "completed"
    REJECTED = "rejected"
    OPERATIONAL_FAILURE = "operational_failure"


class FinalizerMergeViolation(Enum):
    ELIGIBILITY_NOT_PROTECTED_MERGE = "eligibility_not_protected_merge"
    ELIGIBILITY_INCONSISTENT = "eligibility_inconsistent"
    TRANSPORT_REFUSED = "transport_refused"
    TRANSPORT_SUCCESS_INCONSISTENT = "transport_success_inconsistent"


@dataclass(frozen=True)
class ProtectedMergeRequest:
    repository_id: str
    pr_number: int
    expected_head_sha: str


@dataclass(frozen=True)
class ProtectedMergeTransportResult:
    merged: bool
    merge_commit_sha: Optional[str] = None
    expected_head_satisfied: bool = True


class ProtectedMergeTransport(Protocol):
    def merge_protected(self, request: ProtectedMergeRequest) -> ProtectedMergeTransportResult: ...


@dataclass(frozen=True)
class FinalizerMergeResult:
    status: FinalizerMergeStatus
    violations: Tuple[FinalizerMergeViolation, ...] = ()
    merge_commit_sha: Optional[str] = None


def merge_after_fresh_revalidation(
    eligibility: FinalizerEligibility, transport: ProtectedMergeTransport
) -> FinalizerMergeResult:
    """Invoke one expected-HEAD-protected merge; no discovery or semantic review."""

    if eligibility.path is not FinalizerEligibilityPath.PROTECTED_MERGE:
        return _rejected(FinalizerMergeViolation.ELIGIBILITY_NOT_PROTECTED_MERGE)
    if eligibility.violations or eligibility.unit is None:
        return _rejected(FinalizerMergeViolation.ELIGIBILITY_INCONSISTENT)
    unit = eligibility.unit
    if not all((unit.repository_id, unit.checkpoint_id, unit.branch, unit.substantive_head_sha, unit.current_head_sha)):
        return _rejected(FinalizerMergeViolation.ELIGIBILITY_INCONSISTENT)
    request = ProtectedMergeRequest(unit.repository_id, unit.pr_number, unit.current_head_sha)
    try:
        outcome = transport.merge_protected(request)
    except (OSError, RuntimeError):
        return FinalizerMergeResult(FinalizerMergeStatus.OPERATIONAL_FAILURE)
    if not outcome.merged:
        return _rejected(FinalizerMergeViolation.TRANSPORT_REFUSED)
    if not outcome.expected_head_satisfied or not outcome.merge_commit_sha:
        return _rejected(FinalizerMergeViolation.TRANSPORT_SUCCESS_INCONSISTENT)
    return FinalizerMergeResult(FinalizerMergeStatus.COMPLETED, merge_commit_sha=outcome.merge_commit_sha)


def _rejected(violation):
    return FinalizerMergeResult(FinalizerMergeStatus.REJECTED, (violation,))
