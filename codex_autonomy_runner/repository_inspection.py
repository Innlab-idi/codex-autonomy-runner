"""Deterministic, read-only Git repository inspection."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence, Tuple, Union

from .native_process import NativeProcessResult, PathLike, run_native_process


RepositoryPath = Union[str, PathLike]


@dataclass(frozen=True)
class ChangedPaths:
    """Repository-relative paths, separated by Git's change area."""

    staged: Tuple[str, ...]
    unstaged: Tuple[str, ...]
    untracked: Tuple[str, ...]


@dataclass(frozen=True)
class RepositoryInspection:
    """Read-only snapshot of one repository and its changed paths."""

    root: Path
    head_sha: str
    branch: Optional[str]
    is_detached: bool
    changed_paths: ChangedPaths


class RepositoryInspectionError(RuntimeError):
    """Raised when a read-only Git inspection command cannot complete."""

    def __init__(self, result: NativeProcessResult) -> None:
        self.result = result
        super().__init__(
            "Git inspection command failed with exit code {}: {!r}".format(
                result.returncode, result.argv
            )
        )


def inspect_repository(cwd: RepositoryPath) -> RepositoryInspection:
    """Return an immutable Git snapshot for the repository containing ``cwd``.

    ``root`` is the resolved working-tree root. A non-repository path or a
    repository without a resolvable ``HEAD`` raises ``RepositoryInspectionError``.
    ``NativeProcessLaunchError`` remains distinct when Git cannot be started.
    """

    root = Path(_successful_git_output(("rev-parse", "--show-toplevel"), cwd)).resolve()
    head_sha = _successful_git_output(("rev-parse", "--verify", "HEAD"), root)

    branch_result = _run_git(("symbolic-ref", "--quiet", "--short", "HEAD"), root)
    if branch_result.returncode == 0:
        branch = branch_result.stdout.rstrip("\n")
        is_detached = False
    elif branch_result.returncode == 1:
        branch = None
        is_detached = True
    else:
        raise RepositoryInspectionError(branch_result)

    return RepositoryInspection(
        root=root,
        head_sha=head_sha,
        branch=branch,
        is_detached=is_detached,
        changed_paths=ChangedPaths(
            staged=_changed_paths(("diff", "--cached", "--no-renames", "--name-only", "-z"), root),
            unstaged=_changed_paths(("diff", "--no-renames", "--name-only", "-z"), root),
            untracked=_changed_paths(("ls-files", "--others", "--exclude-standard", "-z"), root),
        ),
    )


def _changed_paths(arguments: Sequence[str], cwd: RepositoryPath) -> Tuple[str, ...]:
    output = _successful_git_output(arguments, cwd)
    if not output:
        return ()
    return tuple(sorted(path for path in output.split("\0") if path))


def _successful_git_output(arguments: Sequence[str], cwd: RepositoryPath) -> str:
    result = _run_git(arguments, cwd)
    if result.returncode != 0:
        raise RepositoryInspectionError(result)
    return result.stdout.rstrip("\n")


def _run_git(arguments: Sequence[str], cwd: RepositoryPath) -> NativeProcessResult:
    return run_native_process(("git", *arguments), cwd=cwd)
