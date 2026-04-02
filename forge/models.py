from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ForgeConfig:
    repo_root: Path
    workspace_dir: Path
    user_data_dir: Path
    output_dir: Path
    prd_file: Path
    bash_runtime: Path
    agent_backend: str
    agent_timeout_seconds: float | None

    def to_dict(self) -> dict[str, str]:
        payload = asdict(self)
        return {key: "" if value is None else str(value) for key, value in payload.items()}
