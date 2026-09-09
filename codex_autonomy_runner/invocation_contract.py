"""Minimal internal types for one future runtime invocation."""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class InvocationOutcome(Enum):
    """Structured operational outcome of one invocation."""

    COMPLETED_AUTHORIZED_TRANSITION = "completed_authorized_transition"
    NO_OP = "no_op"
    BLOCKED = "blocked"
    RUNTIME_EXECUTION_FAILURE = "runtime_execution_failure"
    INTERRUPTED_OR_UNTRUSTWORTHY = "interrupted_or_untrustworthy"


@dataclass(frozen=True)
class InvocationRequest:
    """Data for an invocation already directed to one repository and ref."""

    repository: Path
    intended_ref: str


@dataclass(frozen=True)
class InvocationResult:
    """Structured result whose outcome is independent of diagnostic prose."""

    outcome: InvocationOutcome
