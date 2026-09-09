"""Reusable execution primitives for codex-autonomy-runner."""

from .native_process import NativeProcessLaunchError, NativeProcessResult, run_native_process
from .invocation_contract import InvocationOutcome, InvocationRequest, InvocationResult
from .repository_inspection import (
    ChangedPaths,
    RepositoryInspection,
    RepositoryInspectionError,
    inspect_repository,
)

__all__ = [
    "NativeProcessLaunchError",
    "NativeProcessResult",
    "InvocationOutcome",
    "InvocationRequest",
    "InvocationResult",
    "ChangedPaths",
    "RepositoryInspection",
    "RepositoryInspectionError",
    "inspect_repository",
    "run_native_process",
]
