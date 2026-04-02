from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .prd import PrdDocument


@dataclass(frozen=True, slots=True)
class ArchiveResult:
    archive_path: Path | None
    archived: bool


LAST_BRANCH_FILE = ".forge_last_branch"


def archive_if_needed(
    repo_root: Path,
    workspace_dir: Path,
    prd: PrdDocument,
    archive_dir_name: str = "archive",
) -> ArchiveResult:
    current_branch = prd.branch_name
    last_branch_path = repo_root / LAST_BRANCH_FILE
    last_branch = (
        last_branch_path.read_text(encoding="utf-8").strip() if last_branch_path.exists() else ""
    )

    if last_branch and last_branch != current_branch:
        archive_dir = repo_root / archive_dir_name
        archive_name = f"{datetime.now(UTC).date().isoformat()}-{last_branch.replace('/', '-')}"
        archive_path = archive_dir / archive_name
        archive_path.mkdir(parents=True, exist_ok=True)

        progress_file = workspace_dir / "progress.txt"
        if progress_file.exists():
            shutil.copy2(progress_file, archive_path / "progress.txt")
        if prd.path.exists():
            shutil.copy2(prd.path, archive_path / prd.path.name)

        progress_file.write_text("", encoding="utf-8")
        last_branch_path.write_text(current_branch, encoding="utf-8")
        return ArchiveResult(archive_path=archive_path, archived=True)

    last_branch_path.write_text(current_branch, encoding="utf-8")
    return ArchiveResult(archive_path=None, archived=False)
