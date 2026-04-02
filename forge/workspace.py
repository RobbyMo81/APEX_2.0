from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .filesystem import (
    RUNTIME_FILES,
    copy_if_exists,
    ensure_gitignore_entry,
    touch_if_missing,
)


@dataclass(frozen=True, slots=True)
class WorkspaceReport:
    workspace_dir: Path
    migrated_root_gates: bool
    copied_runtime_files: tuple[str, ...]


def ensure_hidden_workspace(
    repo_root: Path,
    workspace_dir: Path,
    source_root: Path,
    gates_file: Path | None = None,
) -> WorkspaceReport:
    workspace_dir.mkdir(parents=True, exist_ok=True)
    resolved_gates_file = gates_file or workspace_dir / "forge.gates.sh"

    copied_runtime_files: list[str] = []
    for runtime_file in RUNTIME_FILES:
        source = source_root / runtime_file
        destination = workspace_dir / runtime_file
        if source.exists():
            copy_if_exists(source, destination)
            copied_runtime_files.append(runtime_file)

    for executable in ("forge.sh", "forge-memory.sh", "forge.gates.example.sh"):
        candidate = workspace_dir / executable
        if candidate.exists():
            candidate.chmod(candidate.stat().st_mode | 0o111)

    root_agents = repo_root / "AGENTS.md"
    workspace_agents = workspace_dir / "AGENTS.md"
    if root_agents.exists() and not workspace_agents.exists():
        copy_if_exists(root_agents, workspace_agents)
    else:
        touch_if_missing(workspace_agents)

    root_progress = repo_root / "progress.txt"
    workspace_progress = workspace_dir / "progress.txt"
    if root_progress.exists() and not workspace_progress.exists():
        copy_if_exists(root_progress, workspace_progress)
    else:
        touch_if_missing(workspace_progress)

    migrated_root_gates = False
    root_gates = repo_root / "forge.gates.sh"
    should_migrate_gates = (
        root_gates.exists()
        and resolved_gates_file != root_gates
        and not resolved_gates_file.exists()
    )
    if should_migrate_gates:
        copy_if_exists(root_gates, resolved_gates_file)
        migrated_root_gates = True

    ensure_gitignore_entry(repo_root, workspace_dir)

    return WorkspaceReport(
        workspace_dir=workspace_dir,
        migrated_root_gates=migrated_root_gates,
        copied_runtime_files=tuple(copied_runtime_files),
    )
