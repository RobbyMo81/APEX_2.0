from __future__ import annotations

from .base import AgentBackend
from .claude import ClaudeBackend
from .codex import CodexBackend


def get_backend(name: str) -> AgentBackend:
    normalized = name.strip().lower()
    if normalized == "claude":
        return ClaudeBackend()
    if normalized == "codex":
        return CodexBackend()
    raise ValueError(f"Unsupported Forge agent backend: {name}")
