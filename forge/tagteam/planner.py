# Co-authored by FORGE (Session: forge-20260402130446-4010642)
"""TagTeam Planner Agent — reads prd.json, invokes Claude, writes tagteam.plan.json.

Safety contract:
- NEVER mutates prd.json.
- NEVER runs quality gates.
- Hard-fails if any dependsOn ID is absent from prd.json before writing output.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..agents.base import AgentTask
from ..agents.claude import ClaudeBackend

_PLAN_VERSION = "1"

# Interface-declaration patterns for re-run source scanning
_INTERFACE_PATTERNS = re.compile(
    r"^(?:"
    r"class\s+\w+"
    r"|def\s+\w+"
    r"|interface\s+\w+"
    r"|type\s+\w+\s*="
    r"|@dataclass"
    r"|pub\s+(?:struct|enum|trait|fn)\s+\w+"
    r"|export\s+(?:interface|type|class|function|const)\s+\w+"
    r")"
)

_SOURCE_GLOBS = ["forge/**/*.py", "src/**/*.ts", "tools/**/*.rs"]
_EXCLUDED_PREFIXES = ("test_",)
_EXCLUDED_PATH_PARTS = {"node_modules", "__pycache__", ".venv", "target"}


def _load_prd(prd_file: Path) -> dict[str, Any]:
    with open(prd_file) as fh:
        return json.load(fh)


def _story_ids(prd: dict[str, Any]) -> set[str]:
    return {s["id"] for s in prd.get("userStories", [])}


def _extract_interface_lines(text: str, max_lines: int = 60) -> str:
    """Return lines that declare a type, class, function, or trait signature."""
    collected: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if _INTERFACE_PATTERNS.match(stripped):
            collected.append(line.rstrip())
        if len(collected) >= max_lines:
            break
    return "\n".join(collected)


def _glob_source_snippets(repo_root: Path) -> dict[str, str]:
    """Glob source files and extract interface-relevant lines for re-run mode."""
    snippets: dict[str, str] = {}
    for pattern in _SOURCE_GLOBS:
        for path in sorted(repo_root.glob(pattern)):
            if path.name.startswith(_EXCLUDED_PREFIXES):
                continue
            if _EXCLUDED_PATH_PARTS.intersection(path.parts):
                continue
            try:
                text = path.read_text(errors="replace")
                lines = _extract_interface_lines(text)
                if lines:
                    snippets[str(path.relative_to(repo_root))] = lines
            except OSError:
                pass
    return snippets


def _build_payload(
    prompt: str,
    prd: dict[str, Any],
    snippets: dict[str, str],
) -> str:
    stories_data = [
        {
            "id": s["id"],
            "title": s["title"],
            "description": s.get("description", ""),
            "technicalNotes": s.get("technicalNotes", ""),
            "implementationPlan": s.get("implementationPlan", []),
            "acceptanceCriteria": s.get("acceptanceCriteria", []),
        }
        for s in prd.get("userStories", [])
    ]

    parts: list[str] = [
        prompt,
        "\n\n---\n\n## PRD Stories\n\n```json\n",
        json.dumps(stories_data, indent=2),
        "\n```\n",
    ]

    if snippets:
        parts.append("\n---\n\n## Existing Source Interfaces (Re-run Mode)\n\n")
        for rel_path, snippet in snippets.items():
            parts.append(f"### {rel_path}\n```\n{snippet}\n```\n\n")

    return "".join(parts)


def _extract_json(text: str) -> dict[str, Any]:
    """Extract first JSON object from Claude output."""
    # Direct parse (Claude --print output is often clean)
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass

    # Fenced ```json block
    match = re.search(r"```json\s*([\s\S]+?)\s*```", text)
    if match:
        return json.loads(match.group(1))

    # Raw JSON object anywhere in the output
    match = re.search(r"(\{[\s\S]+\})", text)
    if match:
        return json.loads(match.group(1))

    raise ValueError("No valid JSON object found in planner output")


def _validate_plan(plan: dict[str, Any], known_ids: set[str]) -> list[str]:
    """Return errors for any dependsOn ID missing from prd.json."""
    errors: list[str] = []
    for story in plan.get("stories", []):
        sid = story.get("storyId", "<unknown>")
        for dep in story.get("dependsOn", []):
            if dep not in known_ids:
                errors.append(
                    f"Story {sid} declares dependsOn '{dep}' "
                    f"which does not exist in prd.json — hard failure"
                )
    return errors


def run_planner(
    prd_file: Path,
    repo_root: Path,
    workspace_dir: Path,
    output_file: Path | None = None,
    rerun: bool = False,
    timeout: float | None = 300.0,
) -> int:
    """Run the TagTeam Planner.

    Reads prd.json (read-only), optionally globs source tree on re-run,
    invokes Claude, validates output, and writes tagteam.plan.json.

    Returns 0 on success, 1 on failure. Never mutates prd.json.
    """
    if output_file is None:
        output_file = repo_root / "tagteam.plan.json"

    prompt_file = workspace_dir / "planner-prompt.md"
    if not prompt_file.exists():
        print(
            f"[PLANNER] ERROR: planner-prompt.md not found at {prompt_file}",
            file=sys.stderr,
        )
        return 1

    # Load PRD — read-only, never written back
    prd = _load_prd(prd_file)
    known_ids = _story_ids(prd)

    if not known_ids:
        print("[PLANNER] ERROR: prd.json contains no userStories", file=sys.stderr)
        return 1

    # Re-run mode: include real source interfaces so confidence can be upgraded
    snippets: dict[str, str] = {}
    if rerun and output_file.exists():
        print("[PLANNER] Re-run mode: reading existing source interfaces...", flush=True)
        snippets = _glob_source_snippets(repo_root)
        print(
            f"[PLANNER] Found {len(snippets)} source files with interface declarations",
            flush=True,
        )

    prompt = prompt_file.read_text()
    payload = _build_payload(prompt, prd, snippets)

    story_count = len(prd.get("userStories", []))
    print(
        f"[PLANNER] Invoking Claude backend for {story_count} stories...",
        flush=True,
    )

    backend = ClaudeBackend()
    task = AgentTask(
        story_id="V2-046-PLANNER",
        story_title="TagTeam Planner",
        payload=payload,
        cwd=repo_root,
        env={},
        timeout=timeout,
    )

    result = backend.invoke(task)

    if result.returncode != 0:
        print(
            f"[PLANNER] ERROR: Claude backend exited {result.returncode}",
            file=sys.stderr,
        )
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        return 1

    # Parse JSON from output — never write if parse fails
    try:
        raw_plan = _extract_json(result.stdout)
    except (ValueError, json.JSONDecodeError) as exc:
        print(
            f"[PLANNER] ERROR: Failed to parse planner output as JSON: {exc}",
            file=sys.stderr,
        )
        print(
            f"[PLANNER] Raw output (first 2000 chars):\n{result.stdout[:2000]}",
            file=sys.stderr,
        )
        return 1

    # Safety gate: validate all dependsOn IDs before touching disk
    errors = _validate_plan(raw_plan, known_ids)
    if errors:
        for err in errors:
            print(f"[PLANNER] HARD FAILURE: {err}", file=sys.stderr)
        print(
            "[PLANNER] Aborting — tagteam.plan.json NOT written",
            file=sys.stderr,
        )
        return 1

    plan: dict[str, Any] = {
        "version": _PLAN_VERSION,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "rerun": rerun,
        "sourceFilesConsulted": sorted(snippets.keys()),
        "stories": raw_plan.get("stories", []),
    }

    output_file.write_text(json.dumps(plan, indent=2) + "\n")
    print(f"[PLANNER] Written: {output_file}", flush=True)
    return 0
