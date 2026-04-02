from __future__ import annotations

import os
from pathlib import Path

from .models import ForgeConfig

DEFAULT_USER_DATA_DIR = Path("/home/spoq/t7shield/Documents/forge/mnt/user-data")
DEFAULT_OUTPUT_DIR = DEFAULT_USER_DATA_DIR / "outputs"
DEFAULT_PRD_FILE = DEFAULT_OUTPUT_DIR / "prd.json"
DEFAULT_WORKSPACE_DIRNAME = ".forge"
DEFAULT_BASH_RUNTIME = Path("forge.sh")
DEFAULT_AGENT_BACKEND = "claude"
DEFAULT_AGENT_TIMEOUT_SECONDS = 300.0


def find_repo_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return current


def load_config(start: Path | None = None) -> ForgeConfig:
    repo_root = find_repo_root(start)
    workspace_value = os.environ.get("FORGE_WORKSPACE_DIR", DEFAULT_WORKSPACE_DIRNAME)
    workspace_path = Path(workspace_value)
    workspace_dir = workspace_path if workspace_path.is_absolute() else repo_root / workspace_path

    user_data_dir = Path(
        os.environ.get("FORGE_USER_DATA_DIR", str(DEFAULT_USER_DATA_DIR))
    ).expanduser()
    output_dir = Path(os.environ.get("FORGE_OUTPUT_DIR", str(DEFAULT_OUTPUT_DIR))).expanduser()
    prd_file = Path(os.environ.get("FORGE_PRD_FILE", str(DEFAULT_PRD_FILE))).expanduser()

    bash_runtime_value = os.environ.get("FORGE_BASH_RUNTIME", str(DEFAULT_BASH_RUNTIME))
    bash_runtime_path = Path(bash_runtime_value)
    bash_runtime = (
        bash_runtime_path if bash_runtime_path.is_absolute() else repo_root / bash_runtime_path
    )
    agent_backend = os.environ.get("FORGE_AGENT_BACKEND", DEFAULT_AGENT_BACKEND).strip().lower()
    timeout_value = os.environ.get("FORGE_AGENT_TIMEOUT_SECONDS", str(DEFAULT_AGENT_TIMEOUT_SECONDS)).strip()
    agent_timeout_seconds = float(timeout_value) if timeout_value else None

    return ForgeConfig(
        repo_root=repo_root,
        workspace_dir=workspace_dir,
        user_data_dir=user_data_dir,
        output_dir=output_dir,
        prd_file=prd_file,
        bash_runtime=bash_runtime,
        agent_backend=agent_backend,
        agent_timeout_seconds=agent_timeout_seconds,
    )
