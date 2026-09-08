"""Deterministic native-process execution with explicit text boundaries."""

from dataclasses import dataclass
from typing import Optional, Sequence, Tuple, Union
import os
import subprocess


PathLike = Union[str, os.PathLike]


@dataclass(frozen=True)
class NativeProcessResult:
    """Captured result from a process that was successfully started."""

    argv: Tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class NativeProcessLaunchError(RuntimeError):
    """Raised when the operating system cannot start the requested process."""

    def __init__(self, argv: Tuple[str, ...], cwd: Optional[PathLike], cause: OSError) -> None:
        self.argv = argv
        self.cwd = cwd
        self.cause = cause
        super().__init__("Could not launch native process: {!r}".format(argv))


def run_native_process(
    argv: Sequence[str], *, cwd: Optional[PathLike] = None
) -> NativeProcessResult:
    """Run ``argv`` without a shell and return its independently captured output.

    A non-zero return code is returned as process data. An ``OSError`` while
    starting the executable is instead raised as ``NativeProcessLaunchError``.
    """

    if isinstance(argv, (str, bytes)):
        raise TypeError("argv must be a sequence of argument strings, not a command string")

    normalized_argv = tuple(argv)
    if not normalized_argv:
        raise ValueError("argv must contain an executable")
    if not all(isinstance(argument, str) for argument in normalized_argv):
        raise TypeError("each argv item must be a string")

    try:
        completed = subprocess.run(
            normalized_argv,
            cwd=cwd,
            shell=False,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="strict",
        )
    except OSError as error:
        raise NativeProcessLaunchError(normalized_argv, cwd, error) from error

    return NativeProcessResult(
        argv=normalized_argv,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
