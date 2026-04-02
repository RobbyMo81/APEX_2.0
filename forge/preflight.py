# Co-authored by FORGE (Session: forge-20260328235846-3946349)
from __future__ import annotations

import shutil
from pathlib import Path

from .models import ForgeConfig

# Backends whose CLI availability is validated at preflight time.
# Must stay in sync with forge/agents/factory.py get_backend() values.
SUPPORTED_BACKENDS: tuple[str, ...] = ("claude", "codex")

# Runtime binaries required before orchestration begins (frozen by V2-033).
REQUIRED_BINARIES: tuple[str, ...] = ("jq", "sqlite3", "git")

# Per-file .gitignore suffixes managed under workspace_dir (frozen by V2-033).
_RUNTIME_GITIGNORE_SUFFIXES: tuple[str, ...] = (
    "forge-memory.db",
    "forge-memory.db-shm",
    "forge-memory.db-wal",
    "forge-startup-report.md",
)

_GITIGNORE_COMMENT = "# FORGE memory DB — runtime working directory, not source of record"


class PreflightError(RuntimeError):
    """Raised when a preflight check fails. Message is operator-actionable."""


def validate_backend_cli(backend: str) -> None:
    """Validate that the configured backend CLI is present on PATH.

    Mirrors the backend-agnostic preflight from forge.sh preflight() lines 280-293.
    """
    normalized = backend.strip().lower()
    if normalized not in SUPPORTED_BACKENDS:
        raise PreflightError(
            f"Unsupported FORGE_AGENT_BACKEND value: '{backend}'. "
            f"Supported values: {', '.join(SUPPORTED_BACKENDS)}"
        )
    if normalized == "claude":
        cli = "claude"
        install_hint = "Install: https://docs.claude.com/claude-code"
    else:
        cli = "codex"
        install_hint = "Install the 'codex' CLI for FORGE_AGENT_BACKEND=codex."

    if shutil.which(cli) is None:
        raise PreflightError(
            f"{cli.capitalize()} CLI not found. {install_hint}"
        )


def validate_runtime_binaries(
    binaries: tuple[str, ...] = REQUIRED_BINARIES,
) -> None:
    """Validate that required runtime binaries are available on PATH.

    Mirrors forge.sh preflight() lines 294-296.
    """
    missing = [b for b in binaries if shutil.which(b) is None]
    if missing:
        raise PreflightError(
            f"Missing required binaries: {', '.join(missing)}. "
            "Install them via your package manager."
        )


def validate_prerequisites(prd_file: Path, prompt_file: Path) -> None:
    """Validate that the PRD file and prompt file exist.

    Mirrors forge.sh preflight() lines 298-299.
    """
    if not prd_file.is_file():
        raise PreflightError(
            f"prd.json not found at: {prd_file}. Load the forge skill to generate it."
        )
    if not prompt_file.is_file():
        raise PreflightError(f"prompt.md not found at: {prompt_file}")


def ensure_runtime_gitignore_entries(repo_root: Path, workspace_dir: Path) -> bool:
    """Ensure .gitignore contains the specific runtime entries for forge memory DB files.

    Mirrors forge.sh preflight() lines 303-307.
    Only modifies .gitignore when it exists and the entries are absent.
    Returns True if .gitignore was updated, False otherwise.
    """
    gitignore_path = repo_root / ".gitignore"
    if not gitignore_path.exists():
        return False

    try:
        rel_workspace = workspace_dir.relative_to(repo_root)
    except ValueError:
        rel_workspace = Path(".forge")

    # Use the first suffix as the presence sentinel (same logic as Bash grep check).
    sentinel = f"{rel_workspace.as_posix()}/{_RUNTIME_GITIGNORE_SUFFIXES[0]}"
    current_text = gitignore_path.read_text(encoding="utf-8")
    if sentinel in current_text:
        return False

    entries = "\n".join(
        f"{rel_workspace.as_posix()}/{suffix}" for suffix in _RUNTIME_GITIGNORE_SUFFIXES
    )
    with gitignore_path.open("a", encoding="utf-8") as handle:
        handle.write(f"\n{_GITIGNORE_COMMENT}\n{entries}\n")
    return True


def run_preflight(config: ForgeConfig) -> None:
    """Run all Python-owned preflight checks.

    Covers the same startup conditions as forge.sh preflight() lines 278-309.
    Raises PreflightError with an operator-actionable message on failure.

    NOTE: workspace creation (ensure_hidden_workspace) is handled separately by
    run_once() before this function is called.
    """
    validate_backend_cli(config.agent_backend)
    validate_runtime_binaries()
    prompt_file = config.workspace_dir / "prompt.md"
    validate_prerequisites(config.prd_file, prompt_file)
    ensure_runtime_gitignore_entries(config.repo_root, config.workspace_dir)
