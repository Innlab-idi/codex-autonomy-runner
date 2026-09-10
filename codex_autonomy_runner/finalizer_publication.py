"""Narrow, fail-closed publication of an already allowlisted finalizer closure."""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import subprocess
from typing import Optional, Protocol, Tuple, Union

from .finalizer_closure import (
    ALLOWLISTED_CLOSURE_PATH,
    FinalizerClosurePlan,
    derive_allowlisted_closure,
    validate_allowlisted_closure,
)
from .finalizer_eligibility import FinalizerEligibility, FinalizerEligibilityPath
from .native_process import NativeProcessLaunchError, NativeProcessResult, run_native_process
from .repository_inspection import RepositoryInspection, inspect_repository


RepositoryPath = Union[str, Path]


class FinalizerPublicationStatus(Enum):
    """The publication boundary's three non-semantic outcomes."""

    COMPLETED = "completed"
    REJECTED = "rejected"
    OPERATIONAL_FAILURE = "operational_failure"


class FinalizerPublicationViolation(Enum):
    """Mechanical reasons for a pre-publication rejection."""

    ELIGIBILITY_NOT_CLOSURE_REQUIRED = "eligibility_not_closure_required"
    ELIGIBILITY_INCONSISTENT = "eligibility_inconsistent"
    PLAN_UNIT_MISMATCH = "plan_unit_mismatch"
    PLAN_INCONSISTENT = "plan_inconsistent"
    REPOSITORY_NOT_CLEAN = "repository_not_clean"
    HEAD_MISMATCH = "head_mismatch"
    BRANCH_MISMATCH = "branch_mismatch"
    DETACHED_HEAD = "detached_head"
    SOURCE_BLOB_MISMATCH = "source_blob_mismatch"
    REMOTE_REF_MISSING = "remote_ref_missing"
    REMOTE_REF_MISMATCH = "remote_ref_mismatch"
    REMOTE_REF_AMBIGUOUS = "remote_ref_ambiguous"
    REMOTE_DESTINATION_MISSING = "remote_destination_missing"
    REMOTE_DESTINATION_AMBIGUOUS = "remote_destination_ambiguous"
    POST_WRITE_INVALID = "post_write_invalid"
    POST_STAGE_INVALID = "post_stage_invalid"
    STAGED_BLOB_MISMATCH = "staged_blob_mismatch"
    POST_COMMIT_INVALID = "post_commit_invalid"


class FinalizerPublicationFailureStage(Enum):
    """Stable location of an I/O or transport failure, never a BLOCKED state."""

    INSPECTION = "inspection"
    SOURCE_BLOB = "source_blob"
    REMOTE_RESOLUTION = "remote_resolution"
    REMOTE_PREFLIGHT = "remote_preflight"
    WRITE = "write"
    STAGE = "stage"
    STAGED_BLOB = "staged_blob"
    COMMIT = "commit"
    POST_COMMIT = "post_commit"
    REMOTE_PRE_PUSH = "remote_pre_push"
    PUSH = "push"
    REMOTE_POST_VERIFY = "remote_post_verify"


@dataclass(frozen=True)
class RemoteRefObservation:
    """One exact remote-ref observation, or a mechanically detectable absence."""

    heads: Tuple[str, ...]


@dataclass(frozen=True)
class ResolvedFinalizerPublicationDestination:
    """The sole concrete push endpoint pinned for one publication attempt."""

    push_url: str


@dataclass(frozen=True)
class FinalizerPublicationResult:
    """Structured outcome with a SHA only after matching remote verification."""

    status: FinalizerPublicationStatus
    violations: Tuple[FinalizerPublicationViolation, ...] = ()
    failure_stage: Optional[FinalizerPublicationFailureStage] = None
    local_closure_head_sha: Optional[str] = None
    published_head_sha: Optional[str] = None


class FinalizerPublicationTransport(Protocol):
    """Small finalizer-only seam; it exposes no merge or PR operation."""

    def inspect(self) -> RepositoryInspection: ...
    def read_blob(self, revision: str, path: str) -> str: ...
    def resolve_destination(self) -> ResolvedFinalizerPublicationDestination: ...
    def remote_ref(self, destination: ResolvedFinalizerPublicationDestination, branch: str) -> RemoteRefObservation: ...
    def read_work_queue(self) -> str: ...
    def write_work_queue(self, text: str) -> None: ...
    def stage_work_queue(self) -> NativeProcessResult: ...
    def commit_closure(self, checkpoint_id: str) -> NativeProcessResult: ...
    def parents_of_head(self) -> Tuple[str, ...]: ...
    def changed_paths_of_head(self) -> Tuple[str, ...]: ...
    def push_head_to_branch(self, destination: ResolvedFinalizerPublicationDestination, branch: str) -> NativeProcessResult: ...


@dataclass(frozen=True)
class NativeFinalizerPublicationTransport:
    """Local-Git implementation limited to one supplied remote and branch."""

    repository: Path
    remote: str

    def inspect(self) -> RepositoryInspection:
        return inspect_repository(self.repository)

    def read_blob(self, revision: str, path: str) -> str:
        # subprocess bytes mode deliberately avoids universal-newline translation.
        object_name = revision + path if revision == ":" else "{}:{}".format(revision, path)
        completed = subprocess.run(
            ("git", "show", object_name),
            cwd=self.repository,
            shell=False,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if completed.returncode != 0:
            raise _GitCommandError()
        return completed.stdout.decode("utf-8", errors="strict")

    def resolve_destination(self) -> ResolvedFinalizerPublicationDestination:
        _validate_remote(self.remote)
        result = _git(("remote", "get-url", "--push", "--all", "--", self.remote), self.repository)
        if result.returncode != 0:
            raise _GitCommandError()
        urls = tuple(line for line in result.stdout.splitlines() if line)
        if not urls:
            raise _DestinationError(FinalizerPublicationViolation.REMOTE_DESTINATION_MISSING)
        if len(urls) != 1:
            raise _DestinationError(FinalizerPublicationViolation.REMOTE_DESTINATION_AMBIGUOUS)
        _validate_remote(urls[0])
        return ResolvedFinalizerPublicationDestination(urls[0])

    def remote_ref(self, destination: ResolvedFinalizerPublicationDestination, branch: str) -> RemoteRefObservation:
        result = _git(("ls-remote", "--refs", "--", destination.push_url, "refs/heads/" + branch), self.repository)
        if result.returncode != 0:
            raise _GitCommandError()
        heads = []
        for line in result.stdout.splitlines():
            fields = line.split("\t")
            if len(fields) != 2 or fields[1] != "refs/heads/" + branch or not fields[0]:
                raise _GitCommandError()
            heads.append(fields[0])
        return RemoteRefObservation(tuple(heads))

    def write_work_queue(self, text: str) -> None:
        (self.repository / ALLOWLISTED_CLOSURE_PATH).write_bytes(text.encode("utf-8", errors="strict"))

    def read_work_queue(self) -> str:
        return (self.repository / ALLOWLISTED_CLOSURE_PATH).read_bytes().decode("utf-8", errors="strict")

    def stage_work_queue(self) -> NativeProcessResult:
        return _git(("add", "--", ALLOWLISTED_CLOSURE_PATH), self.repository)

    def commit_closure(self, checkpoint_id: str) -> NativeProcessResult:
        return _git(("commit", "-m", "chore: close " + checkpoint_id), self.repository)

    def parents_of_head(self) -> Tuple[str, ...]:
        output = _successful_output(("show", "-s", "--format=%P", "HEAD"), self.repository)
        return tuple(output.split())

    def changed_paths_of_head(self) -> Tuple[str, ...]:
        output = _successful_output(("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"), self.repository)
        return tuple(sorted(path for path in output.splitlines() if path))

    def push_head_to_branch(self, destination: ResolvedFinalizerPublicationDestination, branch: str) -> NativeProcessResult:
        return _git(("push", "--", destination.push_url, "HEAD:refs/heads/" + branch), self.repository)


class _GitCommandError(RuntimeError):
    pass


class _DestinationError(RuntimeError):
    def __init__(self, violation):
        self.violation = violation


def publish_allowlisted_closure(
    eligibility: FinalizerEligibility,
    plan: FinalizerClosurePlan,
    transport: FinalizerPublicationTransport,
) -> FinalizerPublicationResult:
    """Publish exactly one validated closure without deciding eligibility or merging."""

    rejection = _validate_inputs(eligibility, plan)
    if rejection is not None:
        return rejection
    assert eligibility.unit is not None
    unit = eligibility.unit

    try:
        destination = transport.resolve_destination()
    except _DestinationError as error:
        return _rejected(error.violation)
    except (NativeProcessLaunchError, OSError, RuntimeError):
        return _failure(FinalizerPublicationFailureStage.REMOTE_RESOLUTION)

    try:
        inspection = transport.inspect()
    except (NativeProcessLaunchError, OSError, RuntimeError):
        return _failure(FinalizerPublicationFailureStage.INSPECTION)
    violation = _clean_expected_branch(inspection, unit.substantive_head_sha, unit.branch)
    if violation is not None:
        return _rejected(violation)

    try:
        if transport.read_blob(unit.substantive_head_sha, plan.path) != plan.source_work_queue:
            return _rejected(FinalizerPublicationViolation.SOURCE_BLOB_MISMATCH)
    except (UnicodeError, NativeProcessLaunchError, OSError, RuntimeError):
        return _failure(FinalizerPublicationFailureStage.SOURCE_BLOB)

    try:
        remote = transport.remote_ref(destination, unit.branch)
    except (NativeProcessLaunchError, OSError, RuntimeError):
        return _failure(FinalizerPublicationFailureStage.REMOTE_PREFLIGHT)
    if not remote.heads:
        return _rejected(FinalizerPublicationViolation.REMOTE_REF_MISSING)
    if len(remote.heads) != 1:
        return _rejected(FinalizerPublicationViolation.REMOTE_REF_AMBIGUOUS)
    if remote.heads[0] != unit.substantive_head_sha:
        return _rejected(FinalizerPublicationViolation.REMOTE_REF_MISMATCH)

    try:
        transport.write_work_queue(plan.result_work_queue)
        post_write = transport.inspect()
        actual_candidate = transport.read_work_queue()
    except (UnicodeError, NativeProcessLaunchError, OSError, RuntimeError):
        return _failure(FinalizerPublicationFailureStage.WRITE)
    if _same_head_branch(post_write, unit.substantive_head_sha, unit.branch) is False or not validate_allowlisted_closure(plan, actual_candidate, post_write.changed_paths).is_valid:
        return _rejected(FinalizerPublicationViolation.POST_WRITE_INVALID)

    try:
        staged = transport.stage_work_queue()
    except (NativeProcessLaunchError, OSError, RuntimeError):
        return _failure(FinalizerPublicationFailureStage.STAGE)
    if staged.returncode != 0:
        return _failure(FinalizerPublicationFailureStage.STAGE)
    try:
        post_stage = transport.inspect()
    except (NativeProcessLaunchError, OSError, RuntimeError):
        return _failure(FinalizerPublicationFailureStage.STAGE)
    if _same_head_branch(post_stage, unit.substantive_head_sha, unit.branch) is False or not validate_allowlisted_closure(plan, plan.result_work_queue, post_stage.changed_paths).is_valid:
        return _rejected(FinalizerPublicationViolation.POST_STAGE_INVALID)
    try:
        if transport.read_blob(":", plan.path) != plan.result_work_queue:
            return _rejected(FinalizerPublicationViolation.STAGED_BLOB_MISMATCH)
    except (UnicodeError, NativeProcessLaunchError, OSError, RuntimeError):
        return _failure(FinalizerPublicationFailureStage.STAGED_BLOB)

    try:
        committed = transport.commit_closure(unit.checkpoint_id)
    except (NativeProcessLaunchError, OSError, RuntimeError):
        return _failure(FinalizerPublicationFailureStage.COMMIT)
    if committed.returncode != 0:
        return _failure(FinalizerPublicationFailureStage.COMMIT)
    try:
        post_commit = transport.inspect()
        local_head = post_commit.head_sha
        valid_commit = (
            _clean_expected_branch(post_commit, local_head, unit.branch) is None
            and local_head != unit.substantive_head_sha
            and transport.parents_of_head() == (unit.substantive_head_sha,)
            and transport.changed_paths_of_head() == (plan.path,)
            and transport.read_blob("HEAD", plan.path) == plan.result_work_queue
        )
    except (UnicodeError, NativeProcessLaunchError, OSError, RuntimeError):
        return _failure(FinalizerPublicationFailureStage.POST_COMMIT, _safe_head(transport))
    if not valid_commit:
        return _rejected(FinalizerPublicationViolation.POST_COMMIT_INVALID, local_head)

    try:
        pre_push = transport.remote_ref(destination, unit.branch)
    except (NativeProcessLaunchError, OSError, RuntimeError):
        return _failure(FinalizerPublicationFailureStage.REMOTE_PRE_PUSH, local_head)
    violation = _remote_preflight_violation(pre_push, unit.substantive_head_sha)
    if violation is not None:
        return _rejected(violation, local_head)

    try:
        pushed = transport.push_head_to_branch(destination, unit.branch)
    except (NativeProcessLaunchError, OSError, RuntimeError):
        return _failure(FinalizerPublicationFailureStage.PUSH, local_head)
    if pushed.returncode != 0:
        return _failure(FinalizerPublicationFailureStage.PUSH, local_head)
    try:
        published = transport.remote_ref(destination, unit.branch)
    except (NativeProcessLaunchError, OSError, RuntimeError):
        return _failure(FinalizerPublicationFailureStage.REMOTE_POST_VERIFY, local_head)
    if len(published.heads) != 1 or published.heads[0] != local_head:
        return _failure(FinalizerPublicationFailureStage.REMOTE_POST_VERIFY, local_head)
    return FinalizerPublicationResult(
        FinalizerPublicationStatus.COMPLETED, local_closure_head_sha=local_head,
        published_head_sha=local_head,
    )


def _validate_inputs(eligibility, plan):
    if eligibility.path is not FinalizerEligibilityPath.CLOSURE_REQUIRED:
        return _rejected(FinalizerPublicationViolation.ELIGIBILITY_NOT_CLOSURE_REQUIRED)
    if eligibility.violations or eligibility.unit is None:
        return _rejected(FinalizerPublicationViolation.ELIGIBILITY_INCONSISTENT)
    if eligibility.unit != plan.unit:
        return _rejected(FinalizerPublicationViolation.PLAN_UNIT_MISMATCH)
    derived = derive_allowlisted_closure(eligibility, plan.source_work_queue)
    if not derived.is_valid or derived.plan != plan:
        return _rejected(FinalizerPublicationViolation.PLAN_INCONSISTENT)
    return None


def _clean_expected_branch(inspection, head, branch):
    if inspection.is_detached:
        return FinalizerPublicationViolation.DETACHED_HEAD
    if inspection.branch != branch:
        return FinalizerPublicationViolation.BRANCH_MISMATCH
    if inspection.head_sha != head:
        return FinalizerPublicationViolation.HEAD_MISMATCH
    if any((inspection.changed_paths.staged, inspection.changed_paths.unstaged, inspection.changed_paths.untracked)):
        return FinalizerPublicationViolation.REPOSITORY_NOT_CLEAN
    return None


def _same_head_branch(inspection, head, branch):
    return not inspection.is_detached and inspection.branch == branch and inspection.head_sha == head


def _remote_preflight_violation(remote, expected_head):
    if not remote.heads:
        return FinalizerPublicationViolation.REMOTE_REF_MISSING
    if len(remote.heads) != 1:
        return FinalizerPublicationViolation.REMOTE_REF_AMBIGUOUS
    if remote.heads[0] != expected_head:
        return FinalizerPublicationViolation.REMOTE_REF_MISMATCH
    return None


def _rejected(violation, local_head=None):
    return FinalizerPublicationResult(FinalizerPublicationStatus.REJECTED, (violation,), local_closure_head_sha=local_head)


def _failure(stage, local_head=None):
    return FinalizerPublicationResult(FinalizerPublicationStatus.OPERATIONAL_FAILURE, failure_stage=stage, local_closure_head_sha=local_head)


def _safe_head(transport):
    try:
        return transport.inspect().head_sha
    except (NativeProcessLaunchError, OSError, RuntimeError):
        return None


def _git(arguments, cwd):
    return run_native_process(("git", *arguments), cwd=cwd)


def _successful_output(arguments, cwd):
    result = _git(arguments, cwd)
    if result.returncode != 0:
        raise _GitCommandError()
    return result.stdout.rstrip("\n")


def _validate_remote(remote):
    if not remote or remote.startswith("-"):
        raise _GitCommandError()
