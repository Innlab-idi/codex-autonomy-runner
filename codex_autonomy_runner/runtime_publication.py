"""Mechanical HOST publication of one already validated worker result.

The caller supplies all semantic choices.  Remote and PR effects cross the
small injected transport below; this module deliberately contains no GitHub
client, approval, merge, retry, or worker/check execution capability.
"""

from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha1, sha256
import os
from pathlib import Path
import stat
from typing import Optional, Protocol, Tuple

from .changed_path_validation import validate_changed_paths
from .execution_baseline import ExecutionBaselineStatus
from .invocation_contract import InvocationOutcome, InvocationResult
from .native_process import run_native_process
from .repository_inspection import RepositoryInspection, inspect_repository
from .runtime_invocation import repository_identity
from .runtime_worker import (
    CheckKind, RuntimeWorkerResult, RuntimeWorkerStatus, WorktreeFingerprint,
    WorktreeFingerprintError, fingerprint_worktree,
)


@dataclass(frozen=True)
class RemoteBranchObservation:
    """Exact remote branch state; ``head_sha`` is absent only when absent."""

    exists: bool
    head_sha: Optional[str] = None


@dataclass(frozen=True)
class PushCompletion:
    """A transport result without raw output; unreliable means side effect unknown."""

    completed: bool
    completion_reliable: bool = True


@dataclass(frozen=True)
class PublicationPullRequest:
    number: int
    repository_id: str
    base_ref: str
    head_branch: str
    head_sha: str
    is_open: bool


@dataclass(frozen=True)
class CreatePullRequestRequest:
    repository_id: str
    base_ref: str
    head_branch: str
    head_sha: str
    title: str
    body: str = field(repr=False)


class PublicationTransport(Protocol):
    """Narrow HOST remote/PR boundary; no worker or finalizer authority.

    Implementations may only observe one named branch, non-force-push the
    supplied commit to that branch, and create/observe the exact ordinary PR.
    They must not approve, merge, modify refs by force, or provide generic
    GitHub access.  Exceptions after a mutation attempt mean completion is
    unknown to the caller and are never retried here.
    """

    def observe_remote_branch(
        self, repository_id: str, branch: str
    ) -> RemoteBranchObservation: ...

    def push_non_force(
        self, repository: Path, branch: str, commit_sha: str
    ) -> PushCompletion: ...

    def create_pull_request(self, request: CreatePullRequestRequest) -> PublicationPullRequest: ...

    def observe_pull_request(self, repository_id: str, number: int) -> PublicationPullRequest: ...


class RuntimePublicationStatus(Enum):
    REFUSED = "refused"
    STAGING_UNTRUSTWORTHY = "staging_untrustworthy"
    COMMIT_FAILED = "commit_failed"
    LOCAL_UNTRUSTWORTHY = "local_untrustworthy"
    COMMITTED_UNPUBLISHED = "committed_unpublished"
    REMOTE_UNTRUSTWORTHY = "remote_untrustworthy"
    PR_UNTRUSTWORTHY = "pr_untrustworthy"
    VERIFIED = "verified"


@dataclass(frozen=True)
class RuntimePublicationRequest:
    """Caller-directed publication data; no value is inferred from prose."""

    repository: Path
    repository_id: str
    branch: str
    permitted_paths: Tuple[str, ...]
    required_publication_check_ids: Tuple[str, ...]
    commit_message: str = field(repr=False)
    base_ref: Optional[str] = None
    pr_title: Optional[str] = None
    pr_body: Optional[str] = field(default=None, repr=False)


@dataclass(frozen=True)
class RuntimePublicationResult:
    status: RuntimePublicationStatus
    invocation_result: InvocationResult
    local_commit_sha: Optional[str] = None
    remote_head_sha: Optional[str] = None
    pull_request: Optional[PublicationPullRequest] = None

    @property
    def publication_verified(self) -> bool:
        return self.status is RuntimePublicationStatus.VERIFIED


def _failure(status: RuntimePublicationStatus, *, local=None, remote=None, pr=None, uncertain=False):
    outcome = (InvocationOutcome.INTERRUPTED_OR_UNTRUSTWORTHY
               if uncertain else InvocationOutcome.RUNTIME_EXECUTION_FAILURE)
    return RuntimePublicationResult(status, InvocationResult(outcome), local, remote, pr)


def _valid_sha(value: object) -> bool:
    return isinstance(value, str) and len(value) == 40 and all(c in "0123456789abcdef" for c in value)


def _valid_object_id(value: object) -> bool:
    return (isinstance(value, str) and len(value) in (40, 64)
            and all(c in "0123456789abcdef" for c in value))


def _valid_remote(observation: object) -> bool:
    return (
        isinstance(observation, RemoteBranchObservation)
        and type(observation.exists) is bool
        and ((observation.exists and _valid_sha(observation.head_sha))
             or (not observation.exists and observation.head_sha is None))
    )


def _cleanly_equal_snapshot(left: RepositoryInspection, right: RepositoryInspection) -> bool:
    return left == right


def _git(root: Path, *arguments: str):
    result = run_native_process(("git", *arguments), cwd=root)
    return result


def _git_output(root: Path, *arguments: str) -> Optional[str]:
    result = _git(root, *arguments)
    if result.returncode != 0:
        return None
    return result.stdout.rstrip("\r\n")


def _git_blob_oid(data: bytes, object_format: str) -> str:
    """Return Git's blob object ID for exact bytes, without filter semantics."""

    if not isinstance(data, bytes):
        raise TypeError("Blob data must be bytes")
    algorithms = {"sha1": sha1, "sha256": sha256}
    algorithm = algorithms.get(object_format)
    if algorithm is None:
        raise ValueError("Unsupported Git object format")
    header = b"blob " + str(len(data)).encode("ascii") + b"\0"
    return algorithm(header + data).hexdigest()


def _expected_index_objects(root, expected_paths, fingerprint):
    """Bind every validated path to its exact unfiltered worktree bytes."""

    object_format = _git_output(root, "rev-parse", "--show-object-format")
    if object_format not in ("sha1", "sha256"):
        raise RuntimeError("Unsupported Git object format")
    entries = {entry.path: entry for entry in fingerprint.entries}
    if set(entries) != set(expected_paths) or len(entries) != len(expected_paths):
        raise RuntimeError("Fingerprint path mismatch")
    expected = {}
    for path in expected_paths:
        entry = entries[path]
        candidate = root.joinpath(*Path(path).parts)
        if entry.file_type == "missing":
            try:
                candidate.lstat()
            except FileNotFoundError:
                expected[path] = None
                continue
            raise RuntimeError("Deletion no longer absent")
        info = candidate.lstat()
        if entry.file_type == "regular" and stat.S_ISREG(info.st_mode):
            data = candidate.read_bytes()
        elif entry.file_type == "symlink" and stat.S_ISLNK(info.st_mode):
            data = os.fsencode(os.readlink(candidate))
        else:
            raise RuntimeError("Changed path type moved")
        if sha256(data).hexdigest() != entry.content_sha256:
            raise RuntimeError("Changed bytes moved after freshness validation")
        expected[path] = _git_blob_oid(data, object_format)
    return expected


def _index_matches_expected(root, expected_objects) -> bool:
    """Compare stage-0 index metadata with exact caller-validated blob IDs."""

    result = _git(root, "ls-files", "--stage", "-z", "--", *tuple(expected_objects))
    if result.returncode != 0:
        return False
    observed = {}
    for record in (item for item in result.stdout.split("\0") if item):
        try:
            metadata, path = record.split("\t", 1)
            mode, object_id, stage_number = metadata.split(" ")
        except ValueError:
            return False
        if (not mode or stage_number != "0" or path not in expected_objects
                or path in observed or not _valid_object_id(object_id)):
            return False
        observed[path] = object_id
    for path, expected_object_id in expected_objects.items():
        if expected_object_id is None:
            if path in observed:
                return False
        elif observed.get(path) != expected_object_id:
            return False
    return True


def _valid_pr(pr, request, expected_head, expected_number=None) -> bool:
    return (
        isinstance(pr, PublicationPullRequest)
        and type(pr.number) is int and pr.number > 0
        and (expected_number is None or pr.number == expected_number)
        and pr.repository_id == request.repository_id
        and pr.base_ref == request.base_ref
        and pr.head_branch == request.branch
        and pr.head_sha == expected_head
        and pr.is_open is True
    )


def _required_checks_satisfied(worker: RuntimeWorkerResult, required: Tuple[str, ...]) -> bool:
    if (not isinstance(required, tuple)
            or not all(isinstance(item, str) and item for item in required)
            or len(set(required)) != len(required)):
        return False
    evidence = worker.publication_checks
    seen = {}
    for item in evidence:
        if item.kind is not CheckKind.PUBLICATION:
            return False
        if item.check_id in seen:
            return False
        seen[item.check_id] = item
    return all(identifier in seen and seen[identifier].satisfied for identifier in required)


def _request_is_valid(request: RuntimePublicationRequest) -> bool:
    return (
        isinstance(request, RuntimePublicationRequest)
        and isinstance(request.repository, Path)
        and isinstance(request.repository_id, str) and request.repository_id
        and isinstance(request.branch, str) and request.branch and not request.branch.startswith("-")
        and isinstance(request.permitted_paths, tuple)
        and request.permitted_paths and all(isinstance(path, str) and path for path in request.permitted_paths)
        and len(set(request.permitted_paths)) == len(request.permitted_paths)
        and isinstance(request.commit_message, str) and request.commit_message
    )


def _pr_inputs_are_valid(request, baseline_status) -> bool:
    if not isinstance(request.base_ref, str) or not request.base_ref:
        return False
    if baseline_status is ExecutionBaselineStatus.NEW_WORK:
        # An empty body is explicitly valid; title and base are required.
        return (isinstance(request.pr_title, str) and bool(request.pr_title)
                and isinstance(request.pr_body, str))
    return baseline_status is ExecutionBaselineStatus.EXISTING_WORK


def publish_validated_worker_work(
    request: RuntimePublicationRequest,
    worker: RuntimeWorkerResult,
    transport: PublicationTransport,
) -> RuntimePublicationResult:
    """Publish exactly one fresh caller-authorized worker result.

    All refusals happen before local mutation.  Once a commit exists, no
    operation is retried or rolled back.  A remote/PR side effect that cannot
    be verified is explicitly untrustworthy rather than successful or NO_OP.
    """

    if (not _request_is_valid(request) or not isinstance(worker, RuntimeWorkerResult)
            or not callable(getattr(transport, "observe_remote_branch", None))
            or not callable(getattr(transport, "push_non_force", None))
            or not callable(getattr(transport, "create_pull_request", None))
            or not callable(getattr(transport, "observe_pull_request", None))
            or worker.status is not RuntimeWorkerStatus.VALIDATED
            or not worker.publication_candidate
            or worker.post_worker is None or worker.boundary is None
            or not worker.boundary.is_valid or worker.staged_changes_forbidden
            or worker.worktree_fingerprint is None or worker.repository_id is None
            or not _required_checks_satisfied(worker, request.required_publication_check_ids)):
        return _failure(RuntimePublicationStatus.REFUSED)

    baseline = worker.baseline.baseline
    if (baseline is None or worker.pre_worker is None
            or worker.post_worker.root != worker.pre_worker.root
            or worker.post_worker.is_detached or worker.post_worker.branch != request.branch
            or worker.post_worker.head_sha != baseline.expected_head_sha
            or request.repository.resolve() != worker.post_worker.root):
        return _failure(RuntimePublicationStatus.REFUSED)
    if not _pr_inputs_are_valid(request, worker.baseline.status):
        return _failure(RuntimePublicationStatus.REFUSED)
    if (worker.baseline.status is ExecutionBaselineStatus.EXISTING_WORK
            and (baseline.resolved_ref != request.branch or baseline.pr_number is None)):
        return _failure(RuntimePublicationStatus.REFUSED)

    try:
        fresh = inspect_repository(request.repository)
        if (worker.repository_id != request.repository_id
                or repository_identity(fresh) != request.repository_id
                or not _cleanly_equal_snapshot(fresh, worker.post_worker)
                or fresh.is_detached or fresh.branch != request.branch
                or fresh.head_sha != baseline.expected_head_sha):
            return _failure(RuntimePublicationStatus.REFUSED)
        paths = validate_changed_paths(fresh.changed_paths, request.permitted_paths)
        if (not paths.is_valid or not paths.actual_paths or fresh.changed_paths.staged):
            return _failure(RuntimePublicationStatus.REFUSED)
        fresh_fingerprint = fingerprint_worktree(fresh)
        if fresh_fingerprint != worker.worktree_fingerprint:
            return _failure(RuntimePublicationStatus.REFUSED)
        remote = transport.observe_remote_branch(request.repository_id, request.branch)
        if not _valid_remote(remote):
            return _failure(RuntimePublicationStatus.REFUSED)
        if worker.baseline.status is ExecutionBaselineStatus.EXISTING_WORK:
            if not remote.exists or remote.head_sha != baseline.expected_head_sha:
                return _failure(RuntimePublicationStatus.REFUSED)
            existing_pr = transport.observe_pull_request(
                request.repository_id, baseline.pr_number
            )
            if not _valid_pr(
                existing_pr, request, baseline.expected_head_sha, baseline.pr_number
            ):
                return _failure(RuntimePublicationStatus.REFUSED)
        elif worker.baseline.status is ExecutionBaselineStatus.NEW_WORK:
            if remote.exists:
                return _failure(RuntimePublicationStatus.REFUSED)
        else:
            return _failure(RuntimePublicationStatus.REFUSED)
    except (Exception, KeyboardInterrupt, SystemExit):
        return _failure(RuntimePublicationStatus.REFUSED)

    expected_paths = paths.actual_paths
    try:
        expected_index_objects = _expected_index_objects(
            fresh.root, expected_paths, worker.worktree_fingerprint
        )
    except (Exception, KeyboardInterrupt, SystemExit):
        return _failure(RuntimePublicationStatus.REFUSED)
    try:
        staged = _git(fresh.root, "add", "--", *expected_paths)
    except (Exception, KeyboardInterrupt, SystemExit):
        return _failure(RuntimePublicationStatus.STAGING_UNTRUSTWORTHY, uncertain=True)
    if staged.returncode != 0:
        return _failure(RuntimePublicationStatus.STAGING_UNTRUSTWORTHY, uncertain=True)
    try:
        after_stage = inspect_repository(fresh.root)
        staged_paths = tuple(sorted(set(after_stage.changed_paths.staged)))
        if (staged_paths != expected_paths or after_stage.changed_paths.unstaged
                or after_stage.changed_paths.untracked
                or validate_changed_paths(after_stage.changed_paths, request.permitted_paths).actual_paths != expected_paths
                or fingerprint_worktree(after_stage).content_digest != worker.worktree_fingerprint.content_digest
                or not _index_matches_expected(fresh.root, expected_index_objects)):
            return _failure(RuntimePublicationStatus.STAGING_UNTRUSTWORTHY, uncertain=True)
    except (Exception, KeyboardInterrupt, SystemExit):
        return _failure(RuntimePublicationStatus.STAGING_UNTRUSTWORTHY, uncertain=True)

    commit_uncertain = False
    try:
        commit = _git(fresh.root, "commit", "-m", request.commit_message)
    except (Exception, KeyboardInterrupt, SystemExit):
        commit = None
        commit_uncertain = True

    try:
        local_commit = _git_output(fresh.root, "rev-parse", "--verify", "HEAD")
    except (Exception, KeyboardInterrupt, SystemExit):
        return _failure(RuntimePublicationStatus.LOCAL_UNTRUSTWORTHY, uncertain=True)
    if local_commit == baseline.expected_head_sha:
        return _failure(RuntimePublicationStatus.COMMIT_FAILED)
    if not _valid_sha(local_commit):
        return _failure(RuntimePublicationStatus.LOCAL_UNTRUSTWORTHY,
                        local=local_commit, uncertain=True)
    try:
        parent = _git_output(fresh.root, "rev-parse", "--verify", "HEAD^")
        diff_result = _git(fresh.root, "diff-tree", "--no-commit-id", "--name-only", "-r", "-z", "HEAD")
        commit_paths = tuple(sorted(path for path in diff_result.stdout.split("\0") if path))
        after_commit = inspect_repository(fresh.root)
    except (Exception, KeyboardInterrupt, SystemExit):
        return _failure(RuntimePublicationStatus.LOCAL_UNTRUSTWORTHY,
                        local=local_commit, uncertain=True)
    if (not _valid_sha(local_commit) or parent != baseline.expected_head_sha
            or diff_result.returncode != 0 or commit_paths != expected_paths
            or after_commit.changed_paths.staged or after_commit.changed_paths.unstaged
            or after_commit.changed_paths.untracked):
        return _failure(RuntimePublicationStatus.LOCAL_UNTRUSTWORTHY,
                        local=local_commit, uncertain=True)
    if commit is None or commit_uncertain or commit.returncode != 0:
        return _failure(RuntimePublicationStatus.COMMITTED_UNPUBLISHED,
                        local=local_commit, uncertain=commit_uncertain)

    try:
        pushed = transport.push_non_force(fresh.root, request.branch, local_commit)
    except (Exception, KeyboardInterrupt, SystemExit):
        return _failure(RuntimePublicationStatus.REMOTE_UNTRUSTWORTHY, local=local_commit, uncertain=True)
    if not isinstance(pushed, PushCompletion) or pushed.completion_reliable is not True:
        return _failure(RuntimePublicationStatus.REMOTE_UNTRUSTWORTHY, local=local_commit, uncertain=True)
    if pushed.completed is not True:
        return _failure(RuntimePublicationStatus.COMMITTED_UNPUBLISHED, local=local_commit)
    try:
        verified_remote = transport.observe_remote_branch(request.repository_id, request.branch)
    except (Exception, KeyboardInterrupt, SystemExit):
        return _failure(RuntimePublicationStatus.REMOTE_UNTRUSTWORTHY, local=local_commit, uncertain=True)
    if (not _valid_remote(verified_remote)
            or not verified_remote.exists or verified_remote.head_sha != local_commit):
        return _failure(RuntimePublicationStatus.REMOTE_UNTRUSTWORTHY, local=local_commit,
                        remote=getattr(verified_remote, "head_sha", None), uncertain=True)

    try:
        if worker.baseline.status is ExecutionBaselineStatus.NEW_WORK:
            candidate = transport.create_pull_request(CreatePullRequestRequest(
                request.repository_id, request.base_ref, request.branch, local_commit,
                request.pr_title, request.pr_body,
            ))
            if not _valid_pr(candidate, request, local_commit):
                return _failure(RuntimePublicationStatus.PR_UNTRUSTWORTHY,
                                local=local_commit, remote=local_commit, uncertain=True)
            observed_pr = transport.observe_pull_request(
                request.repository_id, candidate.number
            )
            if not _valid_pr(observed_pr, request, local_commit, candidate.number):
                return _failure(RuntimePublicationStatus.PR_UNTRUSTWORTHY,
                                local=local_commit, remote=local_commit,
                                pr=candidate, uncertain=True)
        else:
            observed_pr = transport.observe_pull_request(
                request.repository_id, baseline.pr_number
            )
            if not _valid_pr(
                observed_pr, request, local_commit, baseline.pr_number
            ):
                return _failure(RuntimePublicationStatus.PR_UNTRUSTWORTHY,
                                local=local_commit, remote=local_commit,
                                pr=observed_pr, uncertain=True)
    except (Exception, KeyboardInterrupt, SystemExit):
        return _failure(RuntimePublicationStatus.PR_UNTRUSTWORTHY,
                        local=local_commit, remote=local_commit, uncertain=True)
    return RuntimePublicationResult(
        RuntimePublicationStatus.VERIFIED,
        InvocationResult(InvocationOutcome.COMPLETED_AUTHORIZED_TRANSITION),
        local_commit, local_commit, observed_pr,
    )
