"""Contained, single-shot Windows executor for already-declared checks only."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
import json
import math
import os
from pathlib import Path, PureWindowsPath
import subprocess
import sys
from typing import Mapping, Optional, Protocol


_SCRUBBER_MARKER = "--codex-check-env-scrubber-v1"
_SCRUBBER_PROTOCOL_MAX_BYTES = 96


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _decode_scrubber_arguments(arguments):
    if type(arguments) is not tuple or len(arguments) != 3 or arguments[0] != _SCRUBBER_MARKER:
        raise ValueError("invalid scrubber invocation")
    environment = json.loads(arguments[1], object_pairs_hook=_strict_object)
    argv = json.loads(arguments[2], object_pairs_hook=_strict_object)
    if (type(environment) is not dict
            or not all(type(key) is str and key and "=" not in key and "\0" not in key
                       and type(value) is str and "\0" not in value
                       for key, value in environment.items())
            or type(argv) is not list or not argv
            or not all(type(value) is str and "\0" not in value for value in argv)
            or not argv[0]):
        raise ValueError("invalid scrubber payload")
    return environment, tuple(argv)


def _run_environment_scrubber(arguments, *, run_child=None, replace_environment=None,
                              current_directory=None, write_protocol=None):
    """Trusted direct command: replace inherited env and emit one sanitized record."""
    run_child = subprocess.run if run_child is None else run_child
    current_directory = os.getcwd if current_directory is None else current_directory
    if replace_environment is None:
        def replace_environment(values):
            os.environ.clear()
            os.environ.update(values)
    write_protocol = sys.stdout.write if write_protocol is None else write_protocol
    record = {"status": "technical_failure"}
    try:
        environment, argv = _decode_scrubber_arguments(arguments)
        replace_environment(environment)
        completion = run_child(
            argv, cwd=current_directory(), env=dict(environment), stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False, check=False,
        )
        if type(completion.returncode) is not int:
            raise ValueError("invalid child completion")
        record = {"status": "check", "returncode": completion.returncode}
    except BaseException:
        record = {"status": "technical_failure"}
    protocol = json.dumps(record, ensure_ascii=True, separators=(",", ":")) + "\n"
    if len(protocol.encode("ascii")) > _SCRUBBER_PROTOCOL_MAX_BYTES:
        protocol = '{"status":"technical_failure"}\n'
    write_protocol(protocol)
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_environment_scrubber(tuple(sys.argv[1:])))


from .codex_worker_executor import (
    CODEX_CLI_VERSION, _credential_paths, _overlap, _toml_key, _windows_path,
    build_worker_environment,
)
from .runtime_worker import CheckCompletion, CheckExecutionContext, CheckKind


class CheckContainmentPreflightStatus(Enum):
    SATISFIED = "satisfied"
    REFUSED = "refused"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class CheckContainmentPreflightResult:
    """Sanitized HOST observation bound only to public check-plan identifiers."""

    status: CheckContainmentPreflightStatus
    attempt_id: Optional[str] = None
    profile_id: Optional[str] = None
    check_id: Optional[str] = None

    def __post_init__(self) -> None:
        if type(self.status) is not CheckContainmentPreflightStatus:
            raise ValueError("invalid check containment preflight status")


@dataclass(frozen=True)
class CodexCheckLaunchPlan:
    """Sanitized launch identity; command and environment stay out of repr."""

    repository: Path
    check_id: str
    attempt_id: str
    profile_id: str
    argv: tuple[str, ...] = field(repr=False)
    environment: tuple[tuple[str, str], ...] = field(repr=False)


class CheckContainmentPreflight(Protocol):
    def verify(self, plan: CodexCheckLaunchPlan) -> CheckContainmentPreflightResult: ...


class _ProcessApi(Protocol):
    def observe_version(self, executable: str, cwd: Path,
                        environment: Mapping[str, str]) -> Optional[str]: ...
    def observe_git_metadata(self, repository: Path,
                             environment: Mapping[str, str]) -> Optional[tuple[Path, Path]]: ...
    def launch(self, argv: tuple[str, ...], cwd: Path,
               environment: Mapping[str, str]): ...
    def cleanup_tree(self, process) -> bool: ...


class _WindowsSandboxProcessApi:
    """Private subprocess implementation; no child output crosses the seam."""

    _OBSERVATION_TIMEOUT_SECONDS = 15
    _CLEANUP_WAIT_SECONDS = 15

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
        if (result.returncode != 0 or result.stderr or len(result.stdout) > 128
                or result.stdout.count("\n") > 1):
            return None
        return result.stdout.rstrip("\r\n")

    def observe_git_metadata(self, repository, environment):
        try:
            result = subprocess.run(
                ("git", "rev-parse", "--path-format=absolute", "--git-dir", "--git-common-dir"),
                cwd=repository, env=dict(environment), stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                errors="strict", timeout=self._OBSERVATION_TIMEOUT_SECONDS, shell=False, check=False,
            )
        except (OSError, subprocess.SubprocessError, UnicodeError):
            return None
        lines = result.stdout.splitlines()
        if result.returncode != 0 or result.stderr or len(lines) != 2:
            return None
        try:
            paths = tuple(Path(line).resolve() for line in lines)
        except (OSError, ValueError):
            return None
        if not all(path.is_absolute() and path.is_dir() for path in paths):
            return None
        return paths

    def launch(self, argv, cwd, environment):
        return subprocess.Popen(
            argv, cwd=cwd, env=dict(environment), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=False,
        )

    def cleanup_tree(self, process) -> bool:
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


def _profile_id(attempt_id: str, check_id: str) -> str:
    material = (attempt_id + "\0" + check_id).encode("utf-8", "strict")
    return "codex-check-" + sha256(material).hexdigest()


def _validate_context(context: object) -> CheckExecutionContext:
    if type(context) is not CheckExecutionContext:
        raise ValueError("check context is invalid")
    if (not isinstance(context.repository, Path) or not context.repository.is_absolute()
            or context.repository.resolve() != context.repository):
        raise ValueError("check repository is invalid")
    if (type(context.check_id) is not str or not context.check_id
            or "\0" in context.check_id or type(context.attempt_id) is not str
            or not context.attempt_id or "\0" in context.attempt_id
            or type(context.kind) is not CheckKind or type(context.argv) is not tuple
            or not context.argv
            or not all(type(item) is str and "\0" not in item for item in context.argv)
            or not context.argv[0]):
        raise ValueError("check context is malformed")
    return context


def _temporary_write_roots(environment: Mapping[str, str]) -> tuple[tuple[str, PureWindowsPath], ...]:
    roots = []
    for name in ("TEMP", "TMP"):
        value = environment.get(name)
        if value is None:
            continue
        text, path = _windows_path(value)
        normalized = str(path)
        if normalized not in (existing for existing, _ in roots):
            roots.append((normalized, path))
    return tuple(roots)


def _filesystem_profile(repository: Path, git_metadata: tuple[Path, Path],
                        environment: Mapping[str, str]) -> str:
    repository_value, repository_path = _windows_path(repository)
    git_values = []
    for directory in git_metadata:
        value, path = _windows_path(directory)
        if not path.is_absolute():
            raise ValueError("Git metadata is structurally invalid")
        normalized = str(path)
        if normalized not in git_values:
            git_values.append(normalized)
    credentials = _credential_paths(environment)
    temporary_roots = _temporary_write_roots(environment)
    protected = (repository_path,) + tuple(_windows_path(value)[1] for value in git_values) + tuple(
        path for _, path in credentials
    )
    if any(_overlap(path, protected_path) for _, path in temporary_roots for protected_path in protected):
        raise ValueError("temporary write root overlaps protected path")
    entries = [(":minimal", "read"), (":root", "read"), (repository_value, "read")]
    entries.extend((value, "write") for value, _ in temporary_roots)
    entries.extend((value, "read") for value in git_values)
    entries.extend((value, "deny") for value, _ in credentials)
    return "{" + ",".join(f"{_toml_key(key)}=\"{access}\"" for key, access in entries) + "}"


class WindowsCodexCheckExecutor:
    """Execute one declared check under `codex sandbox`, never `codex exec`."""

    def __init__(self, containment_preflight: CheckContainmentPreflight, *, codex_path: str,
                 host_environment: Mapping[str, str], timeout_seconds: float = 600,
                 process_api: Optional[_ProcessApi] = None,
                 platform_name: Optional[str] = None) -> None:
        if (not callable(getattr(containment_preflight, "verify", None))
                or type(codex_path) is not str or not codex_path or "\0" in codex_path
                or isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
                or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
            raise ValueError("valid check containment, Codex path, and positive timeout are required")
        _windows_path(codex_path)
        environment = dict(build_worker_environment(host_environment))
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        self._containment_preflight = containment_preflight
        self._codex_path = codex_path
        self._environment = tuple(sorted(environment.items()))
        self._timeout_seconds = timeout_seconds
        self._process_api = process_api or _WindowsSandboxProcessApi()
        self._platform_name = sys.platform if platform_name is None else platform_name

    @staticmethod
    def _technical_failure(*, reliable: bool = True) -> CheckCompletion:
        return CheckCompletion(None, True, reliable)

    @staticmethod
    def _parse_protocol(output: object) -> CheckCompletion:
        if (type(output) is not bytes or not output or len(output) > _SCRUBBER_PROTOCOL_MAX_BYTES
                or output.count(b"\n") != 1 or not output.endswith(b"\n")):
            return CheckCompletion(None, True, False)
        try:
            record = json.loads(output.decode("ascii"), object_pairs_hook=_strict_object)
        except (UnicodeError, ValueError, TypeError):
            return CheckCompletion(None, True, False)
        if type(record) is not dict or type(record.get("status")) is not str:
            return CheckCompletion(None, True, False)
        if set(record) == {"status"} and record["status"] == "technical_failure":
            return CheckCompletion(None, True, True)
        if (set(record) == {"status", "returncode"} and record["status"] == "check"
                and type(record["returncode"]) is int):
            return CheckCompletion(record["returncode"])
        return CheckCompletion(None, True, False)

    def _preflight(self, context: object) -> Optional[tuple[CodexCheckLaunchPlan, dict[str, str]]]:
        if self._platform_name != "win32":
            return None
        try:
            checked = _validate_context(context)
            environment = dict(self._environment)
            metadata = self._process_api.observe_git_metadata(checked.repository, environment)
            if (type(metadata) is not tuple or len(metadata) != 2
                    or not all(isinstance(path, Path) and path.resolve() == path for path in metadata)):
                return None
            profile_id = _profile_id(checked.attempt_id, checked.check_id)
            filesystem = _filesystem_profile(checked.repository, metadata, environment)
            environment_payload = json.dumps(environment, ensure_ascii=False, sort_keys=True,
                                             separators=(",", ":"))
            argv_payload = json.dumps(checked.argv, ensure_ascii=False, separators=(",", ":"))
            argv = (
                self._codex_path, "-c", 'windows.sandbox="elevated"',
                "-c", f"permissions.{profile_id}.filesystem={filesystem}",
                "-c", f"permissions.{profile_id}.network.enabled=false", "sandbox",
                "--permission-profile", profile_id, "--cd", str(checked.repository), "--",
                sys.executable, "-I", "-S", "-B", str(Path(__file__).resolve()),
                _SCRUBBER_MARKER, environment_payload, argv_payload,
            )
            plan = CodexCheckLaunchPlan(checked.repository, checked.check_id,
                                        checked.attempt_id, profile_id, argv,
                                        tuple(sorted(environment.items())))
            containment = self._containment_preflight.verify(plan)
            if not self._is_bound_satisfied(containment, plan):
                return None
            version = self._process_api.observe_version(self._codex_path, checked.repository, environment)
        except BaseException:
            return None
        if version != CODEX_CLI_VERSION:
            return None
        return plan, environment

    @staticmethod
    def _is_bound_satisfied(result: object, plan: CodexCheckLaunchPlan) -> bool:
        return (
            type(result) is CheckContainmentPreflightResult
            and result.status is CheckContainmentPreflightStatus.SATISFIED
            and type(result.attempt_id) is str and result.attempt_id == plan.attempt_id
            and type(result.profile_id) is str and result.profile_id == plan.profile_id
            and type(result.check_id) is str and result.check_id == plan.check_id
        )

    def execute(self, context: CheckExecutionContext) -> CheckCompletion:
        prepared = self._preflight(context)
        if prepared is None:
            return self._technical_failure()
        plan, environment = prepared
        process = None
        try:
            process = self._process_api.launch(plan.argv, plan.repository, environment)
            output, _ = process.communicate(timeout=self._timeout_seconds)
            if type(process.returncode) is not int:
                return self._technical_failure(reliable=False)
            if process.returncode != 0:
                return self._technical_failure(reliable=False)
            return self._parse_protocol(output)
        except (subprocess.TimeoutExpired, KeyboardInterrupt, SystemExit):
            if process is not None:
                self._process_api.cleanup_tree(process)
            return self._technical_failure(reliable=False)
        except BaseException:
            if process is None:
                return self._technical_failure()
            self._process_api.cleanup_tree(process)
            return self._technical_failure(reliable=False)
