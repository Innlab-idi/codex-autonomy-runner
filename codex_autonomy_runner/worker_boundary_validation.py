"""Pure validation of the CODEX WORKER execution boundary."""

from dataclasses import dataclass
from enum import Enum
from typing import Tuple

from .changed_path_validation import ChangedPathValidation
from .repository_inspection import RepositoryInspection


class WorkerBoundaryViolation(Enum):
    """Mechanical invariants required around one worker execution."""

    PRE_WORKER_DIRTY = "pre_worker_dirty"
    REPOSITORY_ROOT_CHANGED = "repository_root_changed"
    HEAD_CHANGED = "head_changed"
    BRANCH_CHANGED = "branch_changed"
    DETACHED_STATE_CHANGED = "detached_state_changed"
    POST_PATHS_MISMATCH = "post_paths_mismatch"
    UNAUTHORIZED_POST_PATHS = "unauthorized_post_paths"


@dataclass(frozen=True)
class WorkerBoundaryValidation:
    """Deterministic mechanical boundary result for caller-supplied snapshots."""

    violations: Tuple[WorkerBoundaryViolation, ...]

    @property
    def is_valid(self) -> bool:
        """Whether no mechanical boundary invariant was violated."""

        return not self.violations


def validate_worker_boundary(
    pre_worker: RepositoryInspection,
    post_worker: RepositoryInspection,
    changed_path_validation: ChangedPathValidation,
) -> WorkerBoundaryValidation:
    """Validate snapshots and path validation without obtaining any new state."""

    violations = []
    if (
        pre_worker.changed_paths.staged
        or pre_worker.changed_paths.unstaged
        or pre_worker.changed_paths.untracked
    ):
        violations.append(WorkerBoundaryViolation.PRE_WORKER_DIRTY)
    if pre_worker.root != post_worker.root:
        violations.append(WorkerBoundaryViolation.REPOSITORY_ROOT_CHANGED)
    if pre_worker.head_sha != post_worker.head_sha:
        violations.append(WorkerBoundaryViolation.HEAD_CHANGED)
    if pre_worker.branch != post_worker.branch:
        violations.append(WorkerBoundaryViolation.BRANCH_CHANGED)
    if pre_worker.is_detached != post_worker.is_detached:
        violations.append(WorkerBoundaryViolation.DETACHED_STATE_CHANGED)
    if _actual_paths(post_worker) != changed_path_validation.actual_paths:
        violations.append(WorkerBoundaryViolation.POST_PATHS_MISMATCH)
    if not changed_path_validation.is_valid:
        violations.append(WorkerBoundaryViolation.UNAUTHORIZED_POST_PATHS)
    return WorkerBoundaryValidation(violations=tuple(violations))


def _actual_paths(inspection: RepositoryInspection) -> Tuple[str, ...]:
    """Derive the CORE-06 logical path inventory from a supplied snapshot."""

    changed_paths = inspection.changed_paths
    return tuple(
        sorted(set(changed_paths.staged) | set(changed_paths.unstaged) | set(changed_paths.untracked))
    )
