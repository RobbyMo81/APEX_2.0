from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class CommandResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_seconds: float


class CommandExecutionError(RuntimeError):
    pass


def run_command(
    args: list[str],
    cwd: Path,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
    check: bool = False,
    input: str | None = None,
) -> CommandResult:
    merged_env = os.environ.copy()
    if env is not None:
        merged_env.update(env)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            args,
            cwd=cwd,
            env=merged_env,
            input=input,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        result = CommandResult(
            args=tuple(args),
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            timed_out=False,
            duration_seconds=time.monotonic() - started,
        )
    except subprocess.TimeoutExpired as error:
        stdout = error.stdout if isinstance(error.stdout, str) else (error.stdout or b"").decode()
        stderr = error.stderr if isinstance(error.stderr, str) else (error.stderr or b"").decode()
        result = CommandResult(
            args=tuple(args),
            returncode=124,
            stdout=stdout,
            stderr=stderr,
            timed_out=True,
            duration_seconds=time.monotonic() - started,
        )
    if check and result.returncode != 0:
        raise CommandExecutionError(
            f"Command failed: {' '.join(args)}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result
