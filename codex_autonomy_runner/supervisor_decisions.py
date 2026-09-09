"""Pure reconciliation of caller-supplied AI SUPERVISOR decision observations."""

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Optional, Tuple


class SupervisorDecisionKind(Enum):
    """The only semantic decision classes represented by this contract."""

    APPROVED = "approved"
    AI_REWORK = "ai_rework"
    HUMAN_REQUIRED = "human_required"


@dataclass(frozen=True)
class SupervisorDecisionObservation:
    """One externally observed decision already classified by another layer."""

    checkpoint_id: str
    pr_number: int
    reviewed_head_sha: str
    decision: SupervisorDecisionKind
    is_current: bool


class SupervisorDecisionMatch(Enum):
    """Cardinality of current decisions applicable to one exact PR and HEAD."""

    NONE = "none"
    UNIQUE = "unique"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class SupervisorDecisionDiscovery:
    """Stable current decision matches for an externally selected work unit."""

    match: SupervisorDecisionMatch
    observations: Tuple[SupervisorDecisionObservation, ...]

    @property
    def unique_observation(self) -> Optional[SupervisorDecisionObservation]:
        """Return the sole observation only when the discovery is ``UNIQUE``."""

        if self.match is SupervisorDecisionMatch.UNIQUE:
            return self.observations[0]
        return None

    @property
    def unique_decision(self) -> Optional[SupervisorDecisionKind]:
        """Return the sole decision kind only when the discovery is ``UNIQUE``."""

        observation = self.unique_observation
        return None if observation is None else observation.decision


def discover_supervisor_decisions(
    checkpoint_id: str,
    pr_number: int,
    substantive_head_sha: str,
    observations: Iterable[SupervisorDecisionObservation],
) -> SupervisorDecisionDiscovery:
    """Return current exact-identity decisions in deterministic mechanical order.

    All matching observations, including duplicates, are retained. Ordering uses
    decision value followed by checkpoint, PR, and exact reviewed HEAD identity.
    No chronology or decision precedence is inferred.
    """

    matches = tuple(
        sorted(
            (
                observation
                for observation in observations
                if observation.is_current
                and observation.checkpoint_id == checkpoint_id
                and observation.pr_number == pr_number
                and observation.reviewed_head_sha == substantive_head_sha
            ),
            key=lambda observation: (
                observation.decision.value,
                observation.checkpoint_id,
                observation.pr_number,
                observation.reviewed_head_sha,
            ),
        )
    )
    if not matches:
        match = SupervisorDecisionMatch.NONE
    elif len(matches) == 1:
        match = SupervisorDecisionMatch.UNIQUE
    else:
        match = SupervisorDecisionMatch.AMBIGUOUS
    return SupervisorDecisionDiscovery(match=match, observations=matches)
