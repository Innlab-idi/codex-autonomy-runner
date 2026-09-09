"""Pure validation of HOST preparation before a CODEX WORKER invocation."""

from dataclasses import dataclass
from enum import Enum
from typing import Tuple

from .execution_baseline import ExecutionBaselineResolution, ExecutionBaselineStatus
from .repository_inspection import RepositoryInspection


class PreWorkerPreparationViolation(Enum):
    """Mechanical invariants for a prepared pre-worker repository snapshot."""

    BASELINE_UNRESOLVED = "baseline_unresolved"
    PREPARED_TREE_DIRTY = "prepared_tree_dirty"
    HEAD_MISMATCH = "head_mismatch"
    DETACHED_HEAD = "detached_head"
    PREPARED_BRANCH_MISSING = "prepared_branch_missing"
    EXISTING_BRANCH_MISMATCH = "existing_branch_mismatch"


@dataclass(frozen=True)
class PreWorkerPreparationValidation:
    """Deterministic result for caller-supplied baseline and snapshot inputs."""

    violations: Tuple[PreWorkerPreparationViolation, ...]

    @property
    def is_valid(self) -> bool:
        """Whether the repository is mechanically ready for worker invocation."""

        return not self.violations


def validate_pre_worker_preparation(
    baseline_resolution: ExecutionBaselineResolution,
    inspection: RepositoryInspection,
) -> PreWorkerPreparationValidation:
    """Validate prepared state without obtaining state or changing the repository.

    NEW_WORK requires a clean, attached branch at the frozen HEAD, but does not
    constrain that branch's name. EXISTING_WORK additionally requires the exact
    reconciled branch. Any other resolution supplies no usable baseline.
    """

    violations = []
    baseline_is_resolved = (
        baseline_resolution.status
        in (ExecutionBaselineStatus.NEW_WORK, ExecutionBaselineStatus.EXISTING_WORK)
        and baseline_resolution.baseline is not None
    )
    if not baseline_is_resolved:
        violations.append(PreWorkerPreparationViolation.BASELINE_UNRESOLVED)

    if (
        inspection.changed_paths.staged
        or inspection.changed_paths.unstaged
        or inspection.changed_paths.untracked
    ):
        violations.append(PreWorkerPreparationViolation.PREPARED_TREE_DIRTY)

    if baseline_is_resolved:
        baseline = baseline_resolution.baseline
        if inspection.head_sha != baseline.expected_head_sha:
            violations.append(PreWorkerPreparationViolation.HEAD_MISMATCH)
        if inspection.is_detached:
            violations.append(PreWorkerPreparationViolation.DETACHED_HEAD)
        if inspection.branch is None:
            violations.append(PreWorkerPreparationViolation.PREPARED_BRANCH_MISSING)
        if (
            baseline_resolution.status is ExecutionBaselineStatus.EXISTING_WORK
            and inspection.branch is not None
            and inspection.branch != baseline.resolved_ref
        ):
            violations.append(PreWorkerPreparationViolation.EXISTING_BRANCH_MISMATCH)

    return PreWorkerPreparationValidation(violations=tuple(violations))
