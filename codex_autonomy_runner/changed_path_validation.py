"""Pure exact-path allowlist validation for externally observed changes."""

from dataclasses import dataclass
from typing import Iterable, Tuple

from .repository_inspection import ChangedPaths


@dataclass(frozen=True)
class ChangedPathValidation:
    """Stable partition of actual changed paths against a caller allowlist."""

    actual_paths: Tuple[str, ...]
    permitted_paths: Tuple[str, ...]
    unexpected_paths: Tuple[str, ...]

    @property
    def is_valid(self) -> bool:
        """Whether every actual path was exactly present in the allowlist."""

        return not self.unexpected_paths


def validate_changed_paths(
    changed_paths: ChangedPaths, allowed_paths: Iterable[str]
) -> ChangedPathValidation:
    """Partition exact changed-path identities in deterministic lexical order.

    This function intentionally applies no path normalization, globbing, prefix
    matching, or policy derivation.
    """

    actual_paths = tuple(
        sorted(set(changed_paths.staged) | set(changed_paths.unstaged) | set(changed_paths.untracked))
    )
    allowed_path_set = set(allowed_paths)
    permitted_paths = tuple(path for path in actual_paths if path in allowed_path_set)
    unexpected_paths = tuple(path for path in actual_paths if path not in allowed_path_set)
    return ChangedPathValidation(
        actual_paths=actual_paths,
        permitted_paths=permitted_paths,
        unexpected_paths=unexpected_paths,
    )
