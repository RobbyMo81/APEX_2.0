from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..process import run_command


@dataclass(frozen=True, slots=True)
class AgentTask:
    story_id: str
    story_title: str
    payload: str
    cwd: Path
    env: Mapping[str, str]
    prompt_file: Path | None = None
    prd_file: Path | None = None
    workspace_dir: Path | None = None
    progress_file: Path | None = None
    startup_report_file: Path | None = None
    story_data: Mapping[str, Any] | None = None
    timeout: float | None = None
    command_template_override: str | None = None
    session_id: str | None = None
    iteration: int | None = None


@dataclass(frozen=True, slots=True)
class BackendExecutionResult:
    backend: str
    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_seconds: float


class AgentBackend(ABC):
    name: str

    @abstractmethod
    def build_command(self, task: AgentTask) -> list[str]:
        raise NotImplementedError

    def build_input(self, task: AgentTask) -> str | None:
        return task.payload

    def invoke(self, task: AgentTask) -> BackendExecutionResult:
        args = self.build_command(task)
        input_data = self.build_input(task)
        result = run_command(
            args,
            cwd=task.cwd,
            env=task.env,
            timeout=task.timeout,
            check=False,
            input=input_data,
        )
        return BackendExecutionResult(
            backend=self.name,
            command=result.args,
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            timed_out=result.timed_out,
            duration_seconds=result.duration_seconds,
        )
