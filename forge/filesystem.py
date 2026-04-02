from __future__ import annotations

import shutil
from pathlib import Path

FORGE_WORKSPACE_MARKER = "FORGE hidden workspace"
RUNTIME_FILES: tuple[str, ...] = (
    "forge.sh",
    "forge-memory.sh",
    "forge-memory-client.ts",
    "prompt.md",
    "FORGE.md",
    "MEMORY_PROTOCOL.md",
    "forge.gates.example.sh",
    "package.json",
    "package-lock.json",
    "tsconfig.json",
    "jest.config.cjs",
)


def copy_if_exists(source: Path, destination: Path) -> None:
    if not source.exists():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def touch_if_missing(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)


def resolve_workspace_ignore_entry(repo_root: Path, workspace_dir: Path) -> str:
    normalized = f"{workspace_dir.as_posix().rstrip('/')}/"
    if workspace_dir.is_absolute():
        try:
            relative = workspace_dir.relative_to(repo_root)
        except ValueError:
            return ".forge/"
        return f"{relative.as_posix().rstrip('/')}/"
    return normalized


def ensure_gitignore_entry(repo_root: Path, workspace_dir: Path) -> None:
    gitignore_path = repo_root / ".gitignore"
    workspace_entry = resolve_workspace_ignore_entry(repo_root, workspace_dir)
    if gitignore_path.exists():
        lines = gitignore_path.read_text().splitlines()
        if workspace_entry in lines:
            return
        with gitignore_path.open("a", encoding="utf-8") as handle:
            handle.write(f"\n# {FORGE_WORKSPACE_MARKER}\n{workspace_entry}\n")
        return
    gitignore_path.write_text(
        f"# {FORGE_WORKSPACE_MARKER}\n{workspace_entry}\n",
        encoding="utf-8",
    )
