"""Pure reconciliation of caller-supplied existing-work observations."""

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Optional, Tuple


@dataclass(frozen=True)
class ExistingWorkObservation:
    """One externally observed work unit with explicit identity data."""

    checkpoint_id: str
    pr_number: int
    branch: str
    head_sha: str
    is_active: bool


class ExistingWorkMatch(Enum):
    """Cardinality of active observations for one selected checkpoint."""

    NONE = "none"
    UNIQUE = "unique"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class ExistingWorkDiscovery:
    """Stable active matches for a checkpoint selected by another layer."""

    match: ExistingWorkMatch
    observations: Tuple[ExistingWorkObservation, ...]

    @property
    def unique_observation(self) -> Optional[ExistingWorkObservation]:
        """Return the sole active observation only when ``match`` is ``UNIQUE``."""

        if self.match is ExistingWorkMatch.UNIQUE:
            return self.observations[0]
        return None


def discover_existing_work(
    checkpoint_id: str, observations: Iterable[ExistingWorkObservation]
) -> ExistingWorkDiscovery:
    """Return active exact-checkpoint matches in deterministic mechanical order.

    Matching preserves every caller-supplied duplicate and sorts by PR number,
    branch, then exact HEAD SHA. It performs no semantic interpretation.
    """

    matches = tuple(
        sorted(
            (
                observation
                for observation in observations
                if observation.is_active and observation.checkpoint_id == checkpoint_id
            ),
            key=lambda observation: (
                observation.pr_number,
                observation.branch,
                observation.head_sha,
            ),
        )
    )
    if not matches:
        match = ExistingWorkMatch.NONE
    elif len(matches) == 1:
        match = ExistingWorkMatch.UNIQUE
    else:
        match = ExistingWorkMatch.AMBIGUOUS
    return ExistingWorkDiscovery(match=match, observations=matches)
