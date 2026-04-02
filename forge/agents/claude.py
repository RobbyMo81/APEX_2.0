from __future__ import annotations

from .base import AgentBackend, AgentTask


class ClaudeBackend(AgentBackend):
    name = "claude"

    def build_command(self, task: AgentTask) -> list[str]:
        if task.command_template_override is not None:
            formatted = task.command_template_override.format(
                story_id=task.story_id,
                story_title=task.story_title,
            )
            return ["bash", "-lc", formatted]
        return [
            "claude",
            "--print",
            "--dangerously-skip-permissions",
        ]

    def build_input(self, task: AgentTask) -> str | None:
        return task.payload
