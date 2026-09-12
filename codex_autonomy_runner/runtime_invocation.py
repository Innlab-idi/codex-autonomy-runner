"""Repository-local mechanical envelope; no work selection or wake sequencing.

The caller supplies a synchronous, trusted mechanical action, not a worker.
It must finish all owned activity before returning or reporting a controlled
failure. This is a lifecycle boundary, not a sandbox for that action.

Locking uses atomic mkdir in Git's common administrative directory. All local
invocations (including linked worktrees) share that directory. No lock contents
carry authority; an existing entry is never stolen. Abrupt process death can
leave the entry behind and requires external reconciliation. Evidence is only
returned in memory: no crash recovery or persistent logging is promised here.
"""

from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
import os
from pathlib import Path
import stat
from typing import Callable, Optional

from .invocation_contract import InvocationOutcome, InvocationRequest, InvocationResult
from .native_process import run_native_process
from .repository_inspection import RepositoryInspection, inspect_repository


class InvocationStage(Enum):
    """Fixed diagnostic locations, never exception messages or policy states."""

    REQUEST = "request"
    REPOSITORY = "repository"
    LOCK = "lock"
    PREFLIGHT = "preflight"
    ACTION = "action"
    RELEASE = "release"


class ControlledInvocationFailure(RuntimeError):
    """Action attests a known failure with no activity still running.

    Unexpected exceptions do NOT provide this assurance. Exception text is
    deliberately never copied into the invocation evidence.
    """


@dataclass(frozen=True)
class RuntimeInvocationContext:
    inspection: RepositoryInspection
    intended_ref: str
    execution_profile: object = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class InvocationEvidence:
    """Local diagnostic facts; callers must supply a non-sensitive ref name.

    Repository identity is a digest of its canonical common Git directory.
    No raw paths, profile values, file contents, process output, environment,
    exception messages, or credentials are recorded. completion_reliable
    describes the action's completion, not semantic success or publication.
    """

    repository_id: Optional[str]
    intended_ref: Optional[str]
    lock_acquired: bool
    lock_released: bool
    action_started: bool
    completion_reliable: bool
    failure_stage: Optional[InvocationStage]


@dataclass(frozen=True)
class RuntimeInvocationReport:
    result: InvocationResult
    evidence: InvocationEvidence


class _RepositoryLock:
    def __init__(self, common_directory: Path) -> None:
        self.path = common_directory / "codex-autonomy-runner.lock"
        self.acquired = False
        self.identity = None

    def acquire(self) -> None:
        # mkdir refuses files, directories, and dangling symlinks alike.
        self.path.mkdir()
        self.acquired = True
        info = self.path.lstat()
        if not stat.S_ISDIR(info.st_mode) or not info.st_ino:
            raise OSError("Uncertain lock identity")
        self.identity = (info.st_dev, info.st_ino)

    def release(self) -> None:
        info = self.path.lstat()
        if (
            self.identity is None
            or not stat.S_ISDIR(info.st_mode)
            or (info.st_dev, info.st_ino) != self.identity
        ):
            raise OSError("Uncertain lock ownership")
        # Never recursively remove: unexpected contents mean uncertain state.
        self.path.rmdir()


def _common_directory(inspection: RepositoryInspection) -> Path:
    observed = run_native_process(
        ("git", "rev-parse", "--git-common-dir"), cwd=inspection.root
    )
    directory = observed.stdout.rstrip("\r\n")
    if observed.returncode != 0 or not directory:
        raise OSError("Git common directory unavailable")
    resolved = (inspection.root / directory).resolve(strict=True)
    if not resolved.is_dir() or resolved == inspection.root:
        raise OSError("Invalid Git common directory")
    # An unusual --separate-git-dir inside visible source must not receive
    # runtime artifacts. Normal .git metadata and external metadata are safe.
    try:
        relative = resolved.relative_to(inspection.root)
    except ValueError:
        pass
    else:
        if os.path.normcase(relative.parts[0]) != os.path.normcase(".git"):
            raise OSError("Git metadata is inside the visible worktree")
    return resolved


def run_repository_invocation(
    request: InvocationRequest,
    action: Callable[[RuntimeInvocationContext], InvocationResult],
    *,
    execution_profile: object = None,
) -> RuntimeInvocationReport:
    """Run one synchronous action under a non-blocking repository lock.

    CORE-03 results supplied by the caller are preserved, including BLOCKED
    only when the caller already has its durable justification. The envelope
    never invents semantic completion or BLOCKED. Invalid results, unexpected
    action exceptions, interrupts, and uncertain release yield
    INTERRUPTED_OR_UNTRUSTWORTHY. Controlled failures and technical rejection
    before action yield RUNTIME_EXECUTION_FAILURE. Nothing is retried.

    intended_ref is only required to be a nonempty string; existence, branch
    selection, cleanliness, Gate, approval and consumer checks are not tested.
    KeyboardInterrupt/SystemExit are explicitly returned as interrupted, not
    as normal completion. Uncatchable process termination cannot return a report.
    """
    stage = InvocationStage.REQUEST
    failure_stage = None
    repository_id = None
    intended_ref = None
    lock = None
    released = False
    started = False
    reliable = False
    result = InvocationResult(InvocationOutcome.RUNTIME_EXECUTION_FAILURE)
    try:
        if (
            not isinstance(request, InvocationRequest)
            or not isinstance(request.intended_ref, str)
            or not request.intended_ref.strip()
            or not callable(action)
        ):
            raise ValueError("Invalid invocation request")
        intended_ref = request.intended_ref
        stage = InvocationStage.REPOSITORY
        initial = inspect_repository(request.repository)
        common = _common_directory(initial)
        repository_id = sha256(os.fsencode(os.path.normcase(str(common)))).hexdigest()
        stage = InvocationStage.LOCK
        lock = _RepositoryLock(common)
        lock.acquire()
        stage = InvocationStage.PREFLIGHT
        inspection = inspect_repository(request.repository)
        if inspection.root != initial.root or _common_directory(inspection) != common:
            raise ValueError("Repository identity moved")
        context = RuntimeInvocationContext(inspection, intended_ref, execution_profile)
        stage = InvocationStage.ACTION
        started = True
        supplied = action(context)
        if not isinstance(supplied, InvocationResult) or not isinstance(
            supplied.outcome, InvocationOutcome
        ):
            raise TypeError("Invalid action result")
        result = supplied
        reliable = supplied.outcome is not InvocationOutcome.INTERRUPTED_OR_UNTRUSTWORTHY
    except ControlledInvocationFailure:
        failure_stage = stage
        reliable = started
        result = InvocationResult(InvocationOutcome.RUNTIME_EXECUTION_FAILURE)
    except (KeyboardInterrupt, SystemExit):
        failure_stage = stage
        result = InvocationResult(InvocationOutcome.INTERRUPTED_OR_UNTRUSTWORTHY)
    except Exception:
        failure_stage = stage
        result = InvocationResult(
            InvocationOutcome.INTERRUPTED_OR_UNTRUSTWORTHY
            if started else InvocationOutcome.RUNTIME_EXECUTION_FAILURE
        )
    finally:
        if lock is not None and lock.acquired:
            try:
                lock.release()
                released = True
            except (Exception, KeyboardInterrupt, SystemExit):
                failure_stage = InvocationStage.RELEASE
                result = InvocationResult(InvocationOutcome.INTERRUPTED_OR_UNTRUSTWORTHY)
    return RuntimeInvocationReport(
        result,
        InvocationEvidence(
            repository_id, intended_ref, lock is not None and lock.acquired,
            released, started, reliable, failure_stage,
        ),
    )
