"""Pure resolution of a frozen execution baseline from observed state."""

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .existing_work import ExistingWorkDiscovery, ExistingWorkMatch
from .invocation_contract import InvocationRequest


@dataclass(frozen=True)
class IntendedRefObservation:
    """An exact HEAD already observed for one intended ref."""

    ref: str
    head_sha: str


class ExecutionBaselineStatus(Enum):
    """Mechanical resolution state for an execution baseline."""

    NEW_WORK = "new_work"
    EXISTING_WORK = "existing_work"
    AMBIGUOUS_EXISTING_WORK = "ambiguous_existing_work"
    INPUT_MISMATCH = "input_mismatch"


@dataclass(frozen=True)
class ExecutionBaseline:
    """The exact ref and HEAD a later preparation layer must preserve."""

    checkpoint_id: str
    resolved_ref: str
    expected_head_sha: str
    pr_number: Optional[int]


@dataclass(frozen=True)
class ExecutionBaselineResolution:
    """A resolved baseline, or an explicit state that safely has none."""

    status: ExecutionBaselineStatus
    baseline: Optional[ExecutionBaseline]

    @property
    def is_resolved(self) -> bool:
        """Whether this resolution exposes an exact baseline."""

        return self.baseline is not None


def resolve_execution_baseline(
    checkpoint_id: str,
    invocation_request: InvocationRequest,
    intended_ref_observation: IntendedRefObservation,
    existing_work: ExistingWorkDiscovery,
) -> ExecutionBaselineResolution:
    """Resolve a baseline without obtaining state or selecting work.

    A unique active work observation retains its exact branch, HEAD, and PR
    identity.  With no active work, the caller's exact intended-ref observation
    supplies the new-work baseline.  Ambiguity and input identity conflicts
    intentionally expose no baseline.
    """

    if intended_ref_observation.ref != invocation_request.intended_ref:
        return ExecutionBaselineResolution(
            status=ExecutionBaselineStatus.INPUT_MISMATCH,
            baseline=None,
        )

    if existing_work.match is ExistingWorkMatch.NONE:
        return ExecutionBaselineResolution(
            status=ExecutionBaselineStatus.NEW_WORK,
            baseline=ExecutionBaseline(
                checkpoint_id=checkpoint_id,
                resolved_ref=invocation_request.intended_ref,
                expected_head_sha=intended_ref_observation.head_sha,
                pr_number=None,
            ),
        )

    if existing_work.match is ExistingWorkMatch.AMBIGUOUS:
        return ExecutionBaselineResolution(
            status=ExecutionBaselineStatus.AMBIGUOUS_EXISTING_WORK,
            baseline=None,
        )

    observation = existing_work.unique_observation
    if observation is None or observation.checkpoint_id != checkpoint_id:
        return ExecutionBaselineResolution(
            status=ExecutionBaselineStatus.INPUT_MISMATCH,
            baseline=None,
        )

    return ExecutionBaselineResolution(
        status=ExecutionBaselineStatus.EXISTING_WORK,
        baseline=ExecutionBaseline(
            checkpoint_id=checkpoint_id,
            resolved_ref=observation.branch,
            expected_head_sha=observation.head_sha,
            pr_number=observation.pr_number,
        ),
    )
