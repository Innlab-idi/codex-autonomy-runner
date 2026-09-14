"""One caller-directed HOST preparation, contained worker attempt, and checks.

The caller owns authorization, fresh RUNTIME-02 observations and repository
exclusion (RUNTIME-01). The injected executor is a trusted HOST adapter: it
must run the worker without GitHub credentials/network or Git-metadata write
access, and join all worker activity before returning. This Python protocol
is not an OS sandbox and must not be implemented by running untrusted code
in the HOST process. No native Codex launcher is provided here.
"""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, Protocol, Tuple

from .changed_path_validation import ChangedPathValidation, validate_changed_paths
from .execution_baseline import (
    ExecutionBaselineResolution, ExecutionBaselineStatus, IntendedRefObservation,
    resolve_execution_baseline,
)
from .existing_work import ExistingWorkDiscovery
from .invocation_contract import InvocationOutcome, InvocationRequest, InvocationResult
from .native_process import run_native_process
from .pre_worker_preparation import (
    PreWorkerPreparationValidation, validate_pre_worker_preparation,
)
from .repository_inspection import RepositoryInspection, inspect_repository
from .worker_boundary_validation import WorkerBoundaryValidation, validate_worker_boundary


class CheckKind(Enum):
    FOCUSED = "focused"
    PUBLICATION = "publication"


@dataclass(frozen=True)
class CheckDeclaration:
    """Caller policy; IDs must be non-sensitive, argv is never evidence."""

    check_id: str
    kind: CheckKind
    argv: Tuple[str, ...] = field(repr=False)

    def __post_init__(self):
        if (not isinstance(self.check_id, str) or not self.check_id.strip()
                or not isinstance(self.kind, CheckKind)
                or not isinstance(self.argv, tuple) or not self.argv
                or not all(isinstance(arg, str) and "\0" not in arg for arg in self.argv)
                or not self.argv[0]):
            raise ValueError("Invalid check declaration")


@dataclass(frozen=True)
class CheckEvidence:
    check_id: str
    kind: CheckKind
    returncode: Optional[int]
    technical_failure: bool = False

    @property
    def satisfied(self) -> bool:
        return self.returncode == 0 and not self.technical_failure


@dataclass(frozen=True)
class CheckExecutionContext:
    """Resolved check data only; no credentials or publication capabilities."""

    repository: Path
    check_id: str
    kind: CheckKind
    argv: Tuple[str, ...] = field(repr=False)
    attempt_id: str


@dataclass(frozen=True)
class CheckCompletion:
    """Sanitized completion; reliable technical failure has no return code.

    Uncertain completion must use completion_reliable=False. No process output,
    environment or exception text crosses this boundary.
    """

    returncode: Optional[int] = None
    technical_failure: bool = False
    completion_reliable: bool = True


class CheckExecutor(Protocol):
    """Injected HOST containment adapter; this protocol is not an OS sandbox.

    Execute only the supplied structured argv in the supplied repository,
    synchronously, finishing all owned activity before returning. Deny network
    access and Git metadata/.git writes; expose no GitHub or HOST publication
    credentials/capabilities. Do not stage, commit, push, modify refs, create or
    update PRs, merge, invoke FINALIZER, or leave background processes running.
    Return only sanitized CheckCompletion, never raw stdout/stderr. Containment
    must be enforced by the HOST adapter, not by executable-name filtering.
    There is deliberately no unrestricted native/default implementation.
    """

    def execute(self, context: CheckExecutionContext) -> CheckCompletion: ...


@dataclass(frozen=True)
class WorkerContext:
    """Only resolved worktree context; caller must exclude secrets/capabilities.

    Opaque instructions/profile are delivered only to the contained executor,
    never included in report evidence. They are not inspected or selected here.
    """

    repository: Path
    checkpoint_id: str
    expected_head_sha: str
    permitted_paths: Tuple[str, ...]
    attempt_id: str
    instructions: object = field(default=None, repr=False, compare=False)
    execution_profile: object = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class WorkerCompletion:
    """Executor attests its contained attempt has terminated reliably."""

    completion_reliable: bool


class WorkerExecutor(Protocol):
    """HOST containment boundary, not authority delegated to the worker.

    Implementations must deny worker Git metadata writes and GitHub access,
    must not pass HOST credentials, and must not leave background work running.
    """

    def execute(self, context: WorkerContext) -> WorkerCompletion: ...


class RuntimeWorkerStatus(Enum):
    PREPARATION_FAILED = "preparation_failed"
    WORKER_UNTRUSTWORTHY = "worker_untrustworthy"
    BOUNDARY_INVALID = "boundary_invalid"
    FOCUSED_UNSATISFIED = "focused_unsatisfied"
    PUBLICATION_UNSATISFIED = "publication_unsatisfied"
    CHECK_RUNTIME_FAILURE = "check_runtime_failure"
    CHECK_UNTRUSTWORTHY = "check_untrustworthy"
    VALIDATED = "validated"


@dataclass(frozen=True)
class RuntimeWorkerResult:
    """Local evidence for later publication consideration, not publication.

    No commands, output, exception messages, instructions or profiles are
    retained. Caller-supplied identifiers and paths must be non-sensitive.
    Empty check tuples mean none declared, never inferred test sufficiency.
    RUNTIME-04 must still revalidate the actual worktree before publishing.
    """

    status: RuntimeWorkerStatus
    invocation_result: Optional[InvocationResult]
    attempt_id: str
    baseline: ExecutionBaselineResolution
    preparation: Optional[PreWorkerPreparationValidation]
    pre_worker: Optional[RepositoryInspection]
    post_worker: Optional[RepositoryInspection]
    paths: Optional[ChangedPathValidation]
    boundary: Optional[WorkerBoundaryValidation]
    staged_changes_forbidden: bool
    worker_started: bool
    focused_checks: Tuple[CheckEvidence, ...]
    publication_checks: Tuple[CheckEvidence, ...]

    @property
    def publication_candidate(self) -> bool:
        return self.status is RuntimeWorkerStatus.VALIDATED


def _git(root, *argv):
    result = run_native_process(("git", *argv), cwd=root)
    if result.returncode != 0:
        raise RuntimeError("Local preparation failed")
    return result.stdout.rstrip("\r\n")


def _prepare(request, resolution, new_branch):
    initial = inspect_repository(request.repository)
    if any((initial.changed_paths.staged, initial.changed_paths.unstaged,
            initial.changed_paths.untracked)):
        raise RuntimeError("Preparation requires a clean worktree")
    baseline = resolution.baseline
    branch = (baseline.resolved_ref
              if resolution.status is ExecutionBaselineStatus.EXISTING_WORK
              else new_branch)
    if (not isinstance(branch, str) or not branch or branch.startswith("-")
            or branch == "HEAD"):
        raise ValueError("An exact branch name is required")
    # Use a fully qualified ref, not --branch shorthand expansion such as @{-1}.
    _git(initial.root, "check-ref-format", "refs/heads/" + branch)
    head = baseline.expected_head_sha
    if not isinstance(head, str) or not head or head.startswith("-"):
        raise ValueError("Missing frozen HEAD")
    if _git(initial.root, "rev-parse", "--verify", "--end-of-options",
            head + "^{commit}") != head:
        raise RuntimeError("Frozen commit unavailable")
    ref = "refs/heads/" + branch
    refs = _git(initial.root, "for-each-ref", "--format=%(refname)")
    exists = ref in refs.splitlines()
    if exists:
        if _git(initial.root, "rev-parse", "--verify", "--end-of-options", ref) != head:
            raise RuntimeError("Divergent branch")
        if initial.branch != branch or initial.is_detached:
            _git(initial.root, "switch", "--no-guess", "--", branch)
    elif resolution.status is ExecutionBaselineStatus.NEW_WORK:
        _git(initial.root, "switch", "--no-guess", "--no-track", "-c", branch, head)
    else:
        raise RuntimeError("Reconciled local branch unavailable")
    prepared = inspect_repository(initial.root)
    validation = validate_pre_worker_preparation(resolution, prepared)
    if prepared.root != initial.root or prepared.branch != branch:
        raise RuntimeError("Prepared repository identity mismatch")
    return prepared, validation


def run_runtime_worker(
    request: InvocationRequest,
    checkpoint_id: str,
    intended_ref: IntendedRefObservation,
    existing_work: ExistingWorkDiscovery,
    executor: WorkerExecutor,
    *,
    permitted_paths: Tuple[str, ...],
    checks: Tuple[CheckDeclaration, ...] = (),
    check_executor: Optional[CheckExecutor] = None,
    new_branch: Optional[str] = None,
    attempt_id: str,
    instructions: object = None,
    execution_profile: object = None,
) -> RuntimeWorkerResult:
    """Consume fresh directed observations; prepare, execute once and inspect.

    Focused checks precede publication checks, preserving caller declaration
    order within each kind. Fail fast without retries. Declared checks require
    a separately injected HOST containment adapter; no native fallback exists.
    Post-check inspection is defense in depth, not the containment mechanism.
    """

    resolution = resolve_execution_baseline(checkpoint_id, request, intended_ref, existing_work)
    preparation = pre = post = paths = boundary = None
    staged = started = False
    focused = []
    publication = []

    def report(status):
        # Success here proves local evidence only. Later composition determines
        # the invocation outcome after HOST publication; neither NO_OP nor a
        # completed durable transition is asserted by this intermediate result.
        outcome = (None if status is RuntimeWorkerStatus.VALIDATED
                   else InvocationOutcome.INTERRUPTED_OR_UNTRUSTWORTHY
                   if status in (RuntimeWorkerStatus.WORKER_UNTRUSTWORTHY,
                                 RuntimeWorkerStatus.CHECK_UNTRUSTWORTHY,
                                 RuntimeWorkerStatus.BOUNDARY_INVALID)
                   else InvocationOutcome.RUNTIME_EXECUTION_FAILURE)
        return RuntimeWorkerResult(
            status, InvocationResult(outcome) if outcome is not None else None,
            attempt_id, resolution, preparation,
            pre, post, paths, boundary, staged, started, tuple(focused), tuple(publication),
        )

    def inspect_boundary():
        nonlocal post, paths, boundary, staged
        post = inspect_repository(pre.root)
        paths = validate_changed_paths(post.changed_paths, permitted_paths)
        boundary = validate_worker_boundary(pre, post, paths)
        staged = bool(post.changed_paths.staged)
        return boundary.is_valid and not staged

    try:
        if (not resolution.is_resolved
                or resolution.status not in (ExecutionBaselineStatus.NEW_WORK,
                                             ExecutionBaselineStatus.EXISTING_WORK)
                or not isinstance(attempt_id, str) or not attempt_id.strip()
                or not isinstance(permitted_paths, tuple)
                or not all(isinstance(path, str) for path in permitted_paths)
                or not isinstance(checks, tuple)
                or not all(isinstance(check, CheckDeclaration) for check in checks)
                or len({check.check_id for check in checks}) != len(checks)
                or not callable(getattr(executor, "execute", None))):
            return report(RuntimeWorkerStatus.PREPARATION_FAILED)
        if checks and not callable(getattr(check_executor, "execute", None)):
            return report(RuntimeWorkerStatus.CHECK_RUNTIME_FAILURE)
        pre, preparation = _prepare(request, resolution, new_branch)
        if not preparation.is_valid:
            return report(RuntimeWorkerStatus.PREPARATION_FAILED)
        context = WorkerContext(pre.root, checkpoint_id, pre.head_sha,
                                permitted_paths, attempt_id, instructions, execution_profile)
        started = True
        completion = executor.execute(context)
        if (not isinstance(completion, WorkerCompletion)
                or completion.completion_reliable is not True):
            return report(RuntimeWorkerStatus.WORKER_UNTRUSTWORTHY)
        if not inspect_boundary():
            return report(RuntimeWorkerStatus.BOUNDARY_INVALID)
        for kind, evidence, failed in (
            (CheckKind.FOCUSED, focused, RuntimeWorkerStatus.FOCUSED_UNSATISFIED),
            (CheckKind.PUBLICATION, publication, RuntimeWorkerStatus.PUBLICATION_UNSATISFIED),
        ):
            for check in checks:
                if check.kind is not kind:
                    continue
                try:
                    result = check_executor.execute(CheckExecutionContext(
                        pre.root, check.check_id, kind, check.argv, attempt_id,
                    ))
                except (Exception, KeyboardInterrupt, SystemExit):
                    return report(RuntimeWorkerStatus.CHECK_UNTRUSTWORTHY)
                if (not isinstance(result, CheckCompletion)
                        or result.completion_reliable is not True
                        or type(result.technical_failure) is not bool
                        or (result.technical_failure and result.returncode is not None)
                        or (not result.technical_failure and type(result.returncode) is not int)):
                    return report(RuntimeWorkerStatus.CHECK_UNTRUSTWORTHY)
                if result.technical_failure:
                    evidence.append(CheckEvidence(check.check_id, kind, None, True))
                    if not inspect_boundary():
                        return report(RuntimeWorkerStatus.BOUNDARY_INVALID)
                    return report(RuntimeWorkerStatus.CHECK_RUNTIME_FAILURE)
                evidence.append(CheckEvidence(check.check_id, kind, result.returncode))
                if not inspect_boundary():
                    return report(RuntimeWorkerStatus.BOUNDARY_INVALID)
                if result.returncode != 0:
                    return report(failed)
        return report(RuntimeWorkerStatus.VALIDATED)
    except (KeyboardInterrupt, SystemExit):
        return report(RuntimeWorkerStatus.WORKER_UNTRUSTWORTHY)
    except Exception:
        return report(RuntimeWorkerStatus.WORKER_UNTRUSTWORTHY if started
                      else RuntimeWorkerStatus.PREPARATION_FAILED)
