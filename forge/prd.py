from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class UserStory:
    id: str
    title: str
    priority: int
    passes: bool
    status: str
    raw: dict[str, Any]


@dataclass(frozen=True, slots=True)
class PrdDocument:
    path: Path
    data: dict[str, Any]

    @property
    def branch_name(self) -> str:
        branch_name = self.data.get("branchName", "forge/feature")
        return branch_name if isinstance(branch_name, str) and branch_name else "forge/feature"

    @property
    def project_name(self) -> str:
        project_name = self.data.get("projectName", "unknown")
        return project_name if isinstance(project_name, str) and project_name else "unknown"

    def stories(self) -> list[UserStory]:
        stories_raw = self.data.get("userStories", [])
        if not isinstance(stories_raw, list):
            return []
        parsed: list[UserStory] = []
        for story_raw in stories_raw:
            if not isinstance(story_raw, dict):
                continue
            story_id = story_raw.get("id")
            title = story_raw.get("title")
            priority = story_raw.get("priority", 9999)
            passes = story_raw.get("passes", False)
            status = self._normalize_story_status(story_raw.get("status"), bool(passes))
            if not isinstance(story_id, str) or not isinstance(title, str):
                continue
            parsed.append(
                UserStory(
                    id=story_id,
                    title=title,
                    priority=int(priority),
                    passes=bool(passes),
                    status=status,
                    raw=dict(story_raw),
                )
            )
        return parsed

    def next_story(self) -> UserStory | None:
        remaining = [story for story in self.stories() if not story.passes]
        if not remaining:
            return None
        return sorted(remaining, key=lambda story: story.priority)[0]

    def mark_story(self, story_id: str, passes: bool) -> None:
        stories_raw = self.data.get("userStories", [])
        if not isinstance(stories_raw, list):
            return
        for story_raw in stories_raw:
            if isinstance(story_raw, dict) and story_raw.get("id") == story_id:
                story_raw["passes"] = passes
                story_raw["status"] = "done" if passes else "todo"

    @staticmethod
    def _normalize_story_status(status: object, passes: bool) -> str:
        if isinstance(status, str) and status in {"todo", "in_progress", "blocked", "done"}:
            return status
        return "done" if passes else "todo"

    def save(self) -> None:
        self.path.write_text(json.dumps(self.data, indent=2) + "\n", encoding="utf-8")


def load_prd(path: Path) -> PrdDocument:
    loaded: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"Invalid PRD payload in {path}")
    return PrdDocument(path=path, data=loaded)
