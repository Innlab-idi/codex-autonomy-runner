"""Offline Codex adapter construction for the generic worker boundary.

This module deliberately has no live process implementation.  A later,
HOST-owned transport may consume its immutable plans after fresh preflight.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
import json
import re
from typing import Mapping, Optional, Protocol, Tuple
from uuid import UUID

from .runtime_worker import WorkerCompletion, WorkerContext


CODEX_CLI_VERSION = "codex-cli 0.156.1"
_HEAD = re.compile(r"^[0-9a-f]{40}$")
_SAFE_ENVIRONMENT_KEYS = (
    "SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "PATHEXT", "USERPROFILE",
    "HOMEDRIVE", "HOMEPATH", "LOCALAPPDATA", "APPDATA", "TEMP", "TMP",
)


@dataclass(frozen=True)
class CodexContainmentAttestation:
    """Opaque caller-supplied technical precondition; it does not authorize work."""

    attestation_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.attestation_id, str) or not self.attestation_id.strip():
            raise ValueError("containment attestation is required")


@dataclass(frozen=True)
class CodexWorkerExecutionProfile:
    """The sole 01A execution policy: elevated sandbox and offline network."""

    containment_attestation: CodexContainmentAttestation
    network_enabled: bool = False

    def __post_init__(self) -> None:
        if (type(self.containment_attestation) is not CodexContainmentAttestation
                or self.network_enabled is not False):
            raise ValueError("unsupported Codex worker execution profile")


@dataclass(frozen=True)
class CodexLaunchPlan:
    """Immutable data for a future injected process transport, never a launcher."""

    repository: Path
    checkpoint_id: str
    expected_head_sha: str
    permitted_paths: Tuple[str, ...]
    attempt_id: str
    profile_id: str
    argv: Tuple[str, ...] = field(repr=False)
    environment: Tuple[Tuple[str, str], ...] = field(repr=False)
    stdin_payload: bytes = field(repr=False)


@dataclass(frozen=True)
class CodexProcessCompletion:
    """Sanitized transport result; process output and exception text are excluded."""

    terminated: bool
    completion_reliable: bool
    returncode: Optional[int] = None
    technical_failure: bool = False
    cleanup_confirmed: bool = True


class CodexProcessTransport(Protocol):
    """Narrow seam for a later contained, synchronous HOST process transport."""

    def execute(self, plan: CodexLaunchPlan) -> CodexProcessCompletion: ...


def _windows_path(value: object) -> tuple[str, PureWindowsPath]:
    if not isinstance(value, (str, Path)):
        raise ValueError("Windows path is invalid")
    text = str(value)
    path = PureWindowsPath(text)
    if (not text or not path.is_absolute() or not re.fullmatch(r"[A-Za-z]:", path.drive)
            or any(ord(char) < 32 or ord(char) == 127 for char in text)
            or any(part in (".", "..") or part.endswith((".", " ")) for part in path.parts[1:])):
        raise ValueError("Windows path is ambiguous")
    return text, path


def _overlap(left: PureWindowsPath, right: PureWindowsPath) -> bool:
    return left == right or left in right.parents or right in left.parents


def _toml_key(value: str) -> str:
    if not isinstance(value, str) or not value or any(0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise ValueError("permission path is invalid")
    return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")


def _credential_paths(environment: Mapping[str, str]) -> tuple[tuple[str, PureWindowsPath], ...]:
    profile = environment.get("USERPROFILE")
    appdata = environment.get("APPDATA")
    if not isinstance(profile, str) or not profile or not isinstance(appdata, str) or not appdata:
        raise ValueError("credential deny paths require USERPROFILE and APPDATA")
    values = (
        str(PureWindowsPath(profile) / ".codex" / "auth.json"),
        str(PureWindowsPath(profile) / ".git-credentials"),
        str(PureWindowsPath(appdata) / "GitHub CLI" / "hosts.yml"),
        str(PureWindowsPath(profile) / ".ssh" / "id_ed25519"),
        str(PureWindowsPath(profile) / ".ssh" / "id_rsa"),
    )
    return tuple(_windows_path(value) for value in values)


def build_worker_environment(source: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    """Create a new allowlisted environment without inspecting excluded values."""
    if not isinstance(source, Mapping):
        raise ValueError("host environment is invalid")
    environment = {}
    for name in _SAFE_ENVIRONMENT_KEYS:
        value = source.get(name)
        if value is not None:
            if not isinstance(value, str):
                raise ValueError("allowlisted environment value is invalid")
            environment[name] = value
    environment["PYTHONNOUSERSITE"] = "1"
    return tuple(sorted(environment.items()))


def _validate_codex_home(source: Mapping[str, str]) -> None:
    value = source.get("CODEX_HOME")
    if value in (None, ""):
        return
    profile = source.get("USERPROFILE")
    if not isinstance(value, str) or not isinstance(profile, str) or not profile:
        raise ValueError("custom CODEX_HOME is refused")
    if _windows_path(value)[1] != _windows_path(str(PureWindowsPath(profile) / ".codex"))[1]:
        raise ValueError("custom CODEX_HOME is refused")


def _validate_context(context: object, profile: CodexWorkerExecutionProfile) -> tuple[Path, UUID, str]:
    if not isinstance(context, WorkerContext) or context.execution_profile is not profile:
        raise ValueError("worker context is not bound to this profile")
    if not isinstance(context.repository, Path) or not context.repository.is_absolute():
        raise ValueError("repository must be absolute")
    repository = context.repository.resolve()
    repository_value, _ = _windows_path(repository)
    if repository_value != str(repository):
        raise ValueError("repository is structurally invalid")
    if not isinstance(context.checkpoint_id, str) or not context.checkpoint_id.strip():
        raise ValueError("checkpoint is invalid")
    if not isinstance(context.expected_head_sha, str) or not _HEAD.fullmatch(context.expected_head_sha):
        raise ValueError("expected HEAD is invalid")
    if (not isinstance(context.permitted_paths, tuple) or not context.permitted_paths
            or len(set(context.permitted_paths)) != len(context.permitted_paths)
            or not all(isinstance(path, str) and path and "\0" not in path
                       and not Path(path).is_absolute() and ".." not in Path(path).parts
                       for path in context.permitted_paths)):
        raise ValueError("permitted paths are invalid")
    if not isinstance(context.instructions, str) or not context.instructions:
        raise ValueError("instructions must be nonempty text")
    try:
        attempt = UUID(context.attempt_id)
    except (ValueError, AttributeError, TypeError):
        raise ValueError("attempt_id must be a canonical UUID") from None
    if context.attempt_id != str(attempt):
        raise ValueError("attempt_id must be a canonical UUID")
    return repository, attempt, context.instructions


def build_codex_launch_plan(
    context: WorkerContext, *, codex_path: str, observed_codex_version: str,
    host_environment: Mapping[str, str], execution_profile: CodexWorkerExecutionProfile,
) -> CodexLaunchPlan:
    """Build the exact 01A plan, with no filesystem mutation or process execution."""
    if (type(execution_profile) is not CodexWorkerExecutionProfile
            or observed_codex_version != CODEX_CLI_VERSION
            or not isinstance(codex_path, str) or not codex_path or "\0" in codex_path):
        raise ValueError("Codex launch inputs are unsupported")
    _windows_path(codex_path)
    repository, attempt, instructions = _validate_context(context, execution_profile)
    _validate_codex_home(host_environment)
    environment = build_worker_environment(host_environment)
    repository_value, repository_path = _windows_path(repository)
    credentials = _credential_paths(host_environment)
    if any(_overlap(repository_path, credential) for _, credential in credentials):
        raise ValueError("credential path overlaps repository")
    if len({str(path).lower() for _, path in credentials}) != len(credentials):
        raise ValueError("credential deny paths overlap")
    profile_id = "codex-worker-" + attempt.hex
    entries = ((":minimal", "read"), (":root", "read"), (repository_value, "write")) + tuple(
        (value, "deny") for value, _ in credentials
    )
    filesystem = "{" + ",".join(f"{_toml_key(key)}=\"{access}\"" for key, access in entries) + "}"
    argv = (
        codex_path, "-c", 'windows.sandbox="elevated"',
        "-c", f"permissions.{profile_id}.filesystem={filesystem}",
        "-c", f"permissions.{profile_id}.network.enabled=false",
        "-c", f'default_permissions="{profile_id}"', "exec", "--json", "--ephemeral",
        "--ignore-rules", "--ignore-user-config", "--cd", repository_value, "-",
    )
    payload = instructions.encode("utf-8", "strict")
    if any(instructions in value for value in argv) or any(instructions in value for _, value in environment):
        raise ValueError("instructions leaked outside stdin")
    return CodexLaunchPlan(repository, context.checkpoint_id, context.expected_head_sha,
                           context.permitted_paths, context.attempt_id, profile_id,
                           argv, environment, payload)


class CodexWorkerExecutor:
    """Concrete generic WorkerExecutor adapter with an injected offline seam."""

    def __init__(self, transport: CodexProcessTransport, *, codex_path: str,
                 observed_codex_version: str, host_environment: Mapping[str, str],
                 execution_profile: CodexWorkerExecutionProfile) -> None:
        if not callable(getattr(transport, "execute", None)):
            raise ValueError("Codex process transport is required")
        self._transport = transport
        self._codex_path = codex_path
        self._observed_codex_version = observed_codex_version
        self._host_environment = host_environment
        self._execution_profile = execution_profile

    def execute(self, context: WorkerContext) -> WorkerCompletion:
        try:
            plan = build_codex_launch_plan(
                context, codex_path=self._codex_path,
                observed_codex_version=self._observed_codex_version,
                host_environment=self._host_environment,
                execution_profile=self._execution_profile,
            )
            result = self._transport.execute(plan)
        except (Exception, KeyboardInterrupt, SystemExit):
            return WorkerCompletion(False)
        reliable = (isinstance(result, CodexProcessCompletion)
                    and result.terminated is True and result.completion_reliable is True
                    and type(result.returncode) is int and result.technical_failure is False
                    and result.cleanup_confirmed is True)
        return WorkerCompletion(reliable)
