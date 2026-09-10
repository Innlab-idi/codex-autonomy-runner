"""Pure derivation and validation of the allowlisted finalizer closure."""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

from .changed_path_validation import validate_changed_paths
from .finalizer_eligibility import (
    EligibleFinalizerUnit,
    FinalizerEligibility,
    FinalizerEligibilityPath,
)
from .repository_inspection import ChangedPaths


ALLOWLISTED_CLOSURE_PATH = "docs/WORK_QUEUE.md"


class FinalizerClosureViolation(Enum):
    """Stable, mechanical reasons a closure cannot be published."""

    ELIGIBILITY_NOT_CLOSURE_REQUIRED = "eligibility_not_closure_required"
    ELIGIBILITY_INCONSISTENT = "eligibility_inconsistent"
    TARGET_ROW_NOT_FOUND = "target_row_not_found"
    TARGET_ROW_DUPLICATE = "target_row_duplicate"
    TARGET_ROW_MALFORMED = "target_row_malformed"
    OBSERVED_PATHS_DUPLICATE = "observed_paths_duplicate"
    OBSERVED_PATHS_INVALID = "observed_paths_invalid"
    OBSERVED_PATHS_MISMATCH = "observed_paths_mismatch"
    PLAN_INCONSISTENT = "plan_inconsistent"
    CANDIDATE_NOOP = "candidate_noop"
    CANDIDATE_CONTENT_MISMATCH = "candidate_content_mismatch"


@dataclass(frozen=True)
class FinalizerClosurePlan:
    """Exact source/result text and identity for one allowlisted closure."""

    unit: EligibleFinalizerUnit
    path: str
    source_work_queue: str
    result_work_queue: str


@dataclass(frozen=True)
class FinalizerClosureResult:
    """Immutable derivation or validation result."""

    plan: Optional[FinalizerClosurePlan]
    violations: Tuple[FinalizerClosureViolation, ...]

    @property
    def is_valid(self) -> bool:
        return not self.violations and self.plan is not None


def derive_allowlisted_closure(
    eligibility: FinalizerEligibility, source_work_queue: str
) -> FinalizerClosureResult:
    """Derive the sole operational ``AI_REVIEW -> DONE`` closure.

    Only a coherent FINALIZER-01 ``CLOSURE_REQUIRED`` result is accepted. The
    source document is treated as opaque text except for the selected
    checkpoint's single operational table row.
    """

    if eligibility.path is not FinalizerEligibilityPath.CLOSURE_REQUIRED:
        return FinalizerClosureResult(
            plan=None,
            violations=(FinalizerClosureViolation.ELIGIBILITY_NOT_CLOSURE_REQUIRED,),
        )
    if eligibility.violations or eligibility.unit is None:
        return FinalizerClosureResult(
            plan=None,
            violations=(FinalizerClosureViolation.ELIGIBILITY_INCONSISTENT,),
        )
    unit = eligibility.unit
    if unit.current_head_sha != unit.substantive_head_sha:
        return FinalizerClosureResult(
            plan=None,
            violations=(FinalizerClosureViolation.ELIGIBILITY_INCONSISTENT,),
        )
    return _derive_for_unit(unit, source_work_queue)


def validate_allowlisted_closure(
    plan: FinalizerClosurePlan,
    candidate_work_queue: str,
    observed_changed_paths: Optional[ChangedPaths] = None,
) -> FinalizerClosureResult:
    """Validate exact candidate text and optional CORE-02 changed-path facts."""

    violations = []
    expected = _derive_for_unit(plan.unit, plan.source_work_queue)
    if plan.path != ALLOWLISTED_CLOSURE_PATH or not expected.is_valid or expected.plan != plan:
        violations.append(FinalizerClosureViolation.PLAN_INCONSISTENT)

    if observed_changed_paths is not None:
        occurrences = sum(
            path == ALLOWLISTED_CLOSURE_PATH
            for paths in (
                observed_changed_paths.staged,
                observed_changed_paths.unstaged,
                observed_changed_paths.untracked,
            )
            for path in paths
        )
        if occurrences != 1:
            violations.append(FinalizerClosureViolation.OBSERVED_PATHS_DUPLICATE)
        path_validation = validate_changed_paths(
            observed_changed_paths, (ALLOWLISTED_CLOSURE_PATH,)
        )
        if not path_validation.is_valid:
            violations.append(FinalizerClosureViolation.OBSERVED_PATHS_INVALID)
        if path_validation.actual_paths != (ALLOWLISTED_CLOSURE_PATH,):
            violations.append(FinalizerClosureViolation.OBSERVED_PATHS_MISMATCH)

    if candidate_work_queue == plan.source_work_queue:
        violations.append(FinalizerClosureViolation.CANDIDATE_NOOP)
    if candidate_work_queue != plan.result_work_queue:
        violations.append(FinalizerClosureViolation.CANDIDATE_CONTENT_MISMATCH)

    return FinalizerClosureResult(
        plan=plan if not violations else None,
        violations=tuple(violations),
    )


def _derive_for_unit(unit: EligibleFinalizerUnit, source: str) -> FinalizerClosureResult:
    lines = source.splitlines(keepends=True)
    matches = []
    malformed = False
    for index, line in enumerate(lines):
        body = _without_line_ending(line)
        if not body.startswith("|"):
            continue
        pipes = tuple(position for position, character in enumerate(body) if character == "|")
        if len(pipes) < 2:
            continue
        first_cell = body[pipes[0] + 1 : pipes[1]]
        if first_cell.strip() != unit.checkpoint_id:
            continue
        if len(pipes) != 6:
            malformed = True
            continue
        matches.append((index, body, pipes))

    if malformed:
        return FinalizerClosureResult(
            plan=None, violations=(FinalizerClosureViolation.TARGET_ROW_MALFORMED,)
        )
    if not matches:
        return FinalizerClosureResult(
            plan=None, violations=(FinalizerClosureViolation.TARGET_ROW_NOT_FOUND,)
        )
    if len(matches) != 1:
        return FinalizerClosureResult(
            plan=None, violations=(FinalizerClosureViolation.TARGET_ROW_DUPLICATE,)
        )

    index, body, pipes = matches[0]
    cells = tuple(body[pipes[n] + 1 : pipes[n + 1]] for n in range(5))
    if cells[1].strip() != "AI" or cells[2].strip() != "AI_REVIEW":
        return FinalizerClosureResult(
            plan=None, violations=(FinalizerClosureViolation.TARGET_ROW_MALFORMED,)
        )

    dependency = (
        "AI SUPERVISOR approved substantive HEAD "
        f"{unit.substantive_head_sha}; closure materialized."
    )
    result_body = _replace_cells(
        body,
        pipes,
        (2, "DONE"),
        (4, dependency),
    )
    result_line = result_body + _line_ending(lines[index])
    result_lines = list(lines)
    result_lines[index] = result_line
    plan = FinalizerClosurePlan(
        unit=unit,
        path=ALLOWLISTED_CLOSURE_PATH,
        source_work_queue=source,
        result_work_queue="".join(result_lines),
    )
    return FinalizerClosureResult(plan=plan, violations=())


def _without_line_ending(line: str) -> str:
    if line.endswith("\r\n"):
        return line[:-2]
    if line.endswith(("\n", "\r")):
        return line[:-1]
    return line


def _line_ending(line: str) -> str:
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith("\n"):
        return "\n"
    if line.endswith("\r"):
        return "\r"
    return ""


def _replace_cells(body: str, pipes: Tuple[int, ...], *replacements) -> str:
    output = body
    for cell_index, value in sorted(replacements, reverse=True):
        start = pipes[cell_index] + 1
        end = pipes[cell_index + 1]
        original = body[start:end]
        leading = original[: len(original) - len(original.lstrip())]
        trailing = original[len(original.rstrip()) :]
        replacement = leading + value + trailing
        output = output[:start] + replacement + output[end:]
    return output
