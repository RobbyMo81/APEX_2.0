from __future__ import annotations

from .base import AgentBackend, AgentTask


class CodexBackend(AgentBackend):
    name = "codex"

    def build_command(self, task: AgentTask) -> list[str]:
        if task.command_template_override is not None:
            formatted = task.command_template_override.format(
                story_id=task.story_id,
                story_title=task.story_title,
            )
            return ["bash", "-lc", formatted]
        return [
            "codex",
            "exec",
        ]

    def build_input(self, task: AgentTask) -> str | None:
        return task.payload
