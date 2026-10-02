"""Live, single-shot Windows transport for an already validated Codex plan.

Containment evidence remains a HOST responsibility supplied through the narrow
preflight protocol below.  Importing this module has no process side effects.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import subprocess
import sys
from typing import Mapping, Optional, Protocol

from .codex_worker_executor import CODEX_CLI_VERSION, CodexLaunchPlan, CodexProcessCompletion


class ContainmentPreflightStatus(Enum):
    SATISFIED = "satisfied"
    REFUSED = "refused"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class ContainmentPreflightResult:
    """Sanitized HOST containment observation; it conveys no private evidence."""

    status: ContainmentPreflightStatus

    def __post_init__(self) -> None:
        if type(self.status) is not ContainmentPreflightStatus:
            raise ValueError("invalid containment preflight status")


class ContainmentPreflight(Protocol):
    def verify(self, plan: CodexLaunchPlan) -> ContainmentPreflightResult: ...


class _ProcessApi(Protocol):
    def observe_version(self, executable: str, cwd: Path,
                        environment: Mapping[str, str]) -> Optional[str]: ...
    def observe_head(self, repository: Path, environment: Mapping[str, str]) -> Optional[str]: ...
    def launch(self, argv: tuple[str, ...], cwd: Path,
               environment: Mapping[str, str]): ...
    def cleanup_tree(self, process) -> bool: ...


class _WindowsProcessApi:
    """Private subprocess implementation; worker output never crosses this module."""

    _OBSERVATION_TIMEOUT_SECONDS = 15
    _CLEANUP_WAIT_SECONDS = 15

    @staticmethod
    def _bounded_output(result) -> Optional[str]:
        if (result.returncode != 0 or result.stderr or len(result.stdout) > 128
                or result.stdout.count("\n") > 1):
            return None
        return result.stdout.rstrip("\r\n")

    def observe_version(self, executable, cwd, environment):
        try:
            result = subprocess.run(
                (executable, "--version"), cwd=cwd, env=dict(environment),
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="strict", timeout=self._OBSERVATION_TIMEOUT_SECONDS,
                shell=False, check=False,
            )
        except (OSError, subprocess.SubprocessError, UnicodeError):
            return None
        return self._bounded_output(result)

    def observe_head(self, repository, environment):
        try:
            result = subprocess.run(
                ("git", "rev-parse", "--verify", "HEAD"), cwd=repository, env=dict(environment),
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="ascii", errors="strict", timeout=self._OBSERVATION_TIMEOUT_SECONDS,
                shell=False, check=False,
            )
        except (OSError, subprocess.SubprocessError, UnicodeError):
            return None
        return self._bounded_output(result)

    def launch(self, argv, cwd, environment):
        return subprocess.Popen(
            argv, cwd=cwd, env=dict(environment), stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False,
        )

    def cleanup_tree(self, process) -> bool:
        """Use Windows taskkill tree semantics and then observe parent termination."""
        try:
            cleanup = subprocess.run(
                ("taskkill", "/PID", str(process.pid), "/T", "/F"),
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=self._CLEANUP_WAIT_SECONDS, shell=False, check=False,
            )
            if cleanup.returncode != 0:
                return False
            process.wait(timeout=self._CLEANUP_WAIT_SECONDS)
            return type(process.returncode) is int
        except (OSError, subprocess.SubprocessError):
            return False


class WindowsCodexProcessTransport:
    """One synchronous launch after fresh, positive containment and runtime checks."""

    def __init__(self, containment_preflight: ContainmentPreflight, *, timeout_seconds: float = 600,
                 process_api: Optional[_ProcessApi] = None, platform_name: Optional[str] = None) -> None:
        if (not callable(getattr(containment_preflight, "verify", None))
                or isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
                or timeout_seconds <= 0):
            raise ValueError("valid containment preflight and positive timeout are required")
        self._containment_preflight = containment_preflight
        self._timeout_seconds = timeout_seconds
        self._process_api = process_api or _WindowsProcessApi()
        self._platform_name = sys.platform if platform_name is None else platform_name

    @staticmethod
    def _failure(*, cleanup_confirmed: bool = True,
                 completion_reliable: bool = False, returncode: Optional[int] = None) -> CodexProcessCompletion:
        return CodexProcessCompletion(False, completion_reliable, returncode, True, cleanup_confirmed)

    def _preflight(self, plan: object) -> Optional[tuple[CodexLaunchPlan, dict[str, str]]]:
        if type(plan) is not CodexLaunchPlan or self._platform_name != "win32":
            return None
        if not isinstance(plan.repository, Path) or plan.repository.resolve() != plan.repository:
            return None
        try:
            environment = dict(plan.environment)
        except (TypeError, ValueError):
            return None
        if (len(environment) != len(plan.environment) or not all(
                isinstance(key, str) and isinstance(value, str) for key, value in plan.environment)):
            return None
        try:
            containment = self._containment_preflight.verify(plan)
        except BaseException:
            return None
        if (type(containment) is not ContainmentPreflightResult
                or containment.status is not ContainmentPreflightStatus.SATISFIED):
            return None
        try:
            version = self._process_api.observe_version(plan.argv[0], plan.repository, environment)
            head = self._process_api.observe_head(plan.repository, environment)
        except BaseException:
            return None
        if version != CODEX_CLI_VERSION or head != plan.expected_head_sha:
            return None
        return plan, environment

    def execute(self, plan: CodexLaunchPlan) -> CodexProcessCompletion:
        prepared = self._preflight(plan)
        if prepared is None:
            return self._failure()
        prepared_plan, environment = prepared
        process = None
        try:
            process = self._process_api.launch(prepared_plan.argv, prepared_plan.repository, environment)
            returncode = process.communicate(input=prepared_plan.stdin_payload,
                                             timeout=self._timeout_seconds)
            if type(process.returncode) is not int:
                return self._failure(cleanup_confirmed=False)
            return CodexProcessCompletion(True, True, process.returncode, False, True)
        except subprocess.TimeoutExpired:
            cleanup = process is not None and self._process_api.cleanup_tree(process)
            return self._failure(cleanup_confirmed=cleanup, completion_reliable=cleanup,
                                 returncode=(process.returncode if cleanup else None))
        except (KeyboardInterrupt, SystemExit):
            cleanup = process is not None and self._process_api.cleanup_tree(process)
            return self._failure(cleanup_confirmed=cleanup, completion_reliable=cleanup,
                                 returncode=(process.returncode if cleanup else None))
        except BaseException:
            if process is not None:
                cleanup = self._process_api.cleanup_tree(process)
                return self._failure(cleanup_confirmed=cleanup, completion_reliable=cleanup,
                                     returncode=(process.returncode if cleanup else None))
            return self._failure()
