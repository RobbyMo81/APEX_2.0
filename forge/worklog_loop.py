from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from .process import run_command

FRONTMATTER_DELIM = "---"


class WorklogFormatError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class WorklogLoopResult:
    status: str
    completed_todos: int
    total_todos: int
    message: str


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _split_frontmatter(content: str) -> tuple[str, str]:
    if not content.startswith(f"{FRONTMATTER_DELIM}\n"):
        raise WorklogFormatError("Worklog must begin with YAML frontmatter delimited by '---'.")

    marker = f"\n{FRONTMATTER_DELIM}\n"
    end_index = content.find(marker, len(FRONTMATTER_DELIM) + 1)
    if end_index == -1:
        raise WorklogFormatError("Could not find closing YAML frontmatter delimiter '---'.")

    frontmatter = content[len(FRONTMATTER_DELIM) + 1 : end_index]
    body = content[end_index + len(marker) :]
    return frontmatter, body


def load_worklog(path: Path) -> tuple[dict[str, Any], str]:
    content = path.read_text(encoding="utf-8")
    frontmatter_raw, body = _split_frontmatter(content)
    loaded: object = yaml.safe_load(frontmatter_raw) or {}
    if not isinstance(loaded, dict):
        raise WorklogFormatError("YAML frontmatter must parse to an object.")
    return loaded, body


def save_worklog(path: Path, frontmatter: dict[str, Any], body: str) -> None:
    serialized_frontmatter = yaml.safe_dump(frontmatter, sort_keys=False).strip()
    serialized = (
        f"{FRONTMATTER_DELIM}\n"
        f"{serialized_frontmatter}\n"
        f"{FRONTMATTER_DELIM}\n\n"
        f"{body.lstrip()}"
    )
    path.write_text(serialized, encoding="utf-8")


def _priority_rank(priority: str) -> int:
    mapping = {
        "critical": 0,
        "high": 1,
        "medium": 2,
        "low": 3,
    }
    return mapping.get(priority.lower(), 4)


def validate_worklog(frontmatter: dict[str, Any]) -> None:
    todos_obj = frontmatter.get("todos")
    if not isinstance(todos_obj, list) or not todos_obj:
        raise WorklogFormatError("Frontmatter must include a non-empty 'todos' list.")

    ids: set[str] = set()
    for todo in todos_obj:
        if not isinstance(todo, dict):
            raise WorklogFormatError("Each todo must be an object.")
        todo_id = todo.get("id")
        if not isinstance(todo_id, str) or not todo_id:
            raise WorklogFormatError("Each todo must include a non-empty string 'id'.")
        if todo_id in ids:
            raise WorklogFormatError(f"Duplicate todo id detected: {todo_id}")
        ids.add(todo_id)

    for todo in todos_obj:
        depends_on = todo.get("depends_on", [])
        if not isinstance(depends_on, list):
            raise WorklogFormatError(f"Todo {todo['id']} has non-list depends_on value.")
        for dependency in depends_on:
            if dependency not in ids:
                raise WorklogFormatError(
                    f"Todo {todo['id']} depends_on missing id '{dependency}'."
                )

    in_progress_count = sum(1 for todo in todos_obj if todo.get("status") == "in_progress")
    if in_progress_count > 1:
        raise WorklogFormatError("Only one todo may be in_progress at a time.")

    for todo in todos_obj:
        if todo.get("status") == "done" and not todo.get("evidence"):
            raise WorklogFormatError(f"Todo {todo['id']} is done but has no evidence entries.")


def _all_todos_done(todos: list[dict[str, Any]]) -> bool:
    return all(todo.get("status") == "done" for todo in todos)


def _next_actionable_todo(todos: list[dict[str, Any]]) -> dict[str, Any] | None:
    done_ids = {todo.get("id") for todo in todos if todo.get("status") == "done"}
    candidates: list[dict[str, Any]] = []
    for todo in todos:
        if todo.get("status") != "todo":
            continue
        dependencies = todo.get("depends_on", [])
        if all(dependency in done_ids for dependency in dependencies):
            candidates.append(todo)

    if not candidates:
        return None

    return sorted(
        candidates,
        key=lambda item: (
            _priority_rank(str(item.get("priority", "medium"))),
            str(item.get("id", "")),
        ),
    )[0]


def _append_cycle_note(body: str, note: str) -> str:
    heading = "## Cycle Notes"
    bullet = f"- {_utc_now()}: {note}"

    if heading not in body:
        suffix = "" if body.endswith("\n") else "\n"
        return f"{body}{suffix}\n{heading}\n\n{bullet}\n"

    pattern = re.compile(r"(^## Cycle Notes\s*$)", re.MULTILINE)
    match = pattern.search(body)
    if not match:
        return body

    insert_at = match.end()
    return f"{body[:insert_at]}\n\n{bullet}{body[insert_at:]}"


def _ensure_closeout(frontmatter: dict[str, Any], body: str) -> tuple[dict[str, Any], str]:
    summary = frontmatter.setdefault("summary", {})
    if not isinstance(summary, dict):
        summary = {}
        frontmatter["summary"] = summary

    worked = summary.get("what_worked_well")
    if not isinstance(worked, list):
        worked = []
        summary["what_worked_well"] = worked

    issues = summary.get("what_caused_issues")
    if not isinstance(issues, list):
        issues = []
        summary["what_caused_issues"] = issues

    if not worked:
        worked.append("All required todos were completed with verification evidence.")

    next_steps = frontmatter.get("next_recommended_steps")
    if not isinstance(next_steps, list) or not next_steps:
        frontmatter["next_recommended_steps"] = [
            "Review evidence artifacts for each completed todo.",
            "Open a new worklog cycle for follow-up enhancements.",
            "Provide the next instruction batch to the agent.",
        ]

    frontmatter["instructions_waiting"] = True
    frontmatter["agent_state"] = "waiting"
    frontmatter["status"] = "awaiting_instructions"
    frontmatter["updated_at"] = _utc_now()

    body = _append_cycle_note(body, "All todos complete. Waiting for instructions.")
    return frontmatter, body


def run_worklog_loop(
    worklog_path: Path,
    work_command: str,
    verify_command: str,
    cwd: Path,
    max_cycles: int = 200,
) -> WorklogLoopResult:
    if max_cycles < 1:
        raise ValueError("max_cycles must be >= 1")

    frontmatter, body = load_worklog(worklog_path)

    for cycle in range(1, max_cycles + 1):
        validate_worklog(frontmatter)
        todos = frontmatter["todos"]
        assert isinstance(todos, list)

        if _all_todos_done(todos):
            frontmatter, body = _ensure_closeout(frontmatter, body)
            save_worklog(worklog_path, frontmatter, body)
            return WorklogLoopResult(
                status="awaiting_instructions",
                completed_todos=len(todos),
                total_todos=len(todos),
                message="All todos completed. Worklog is now waiting for instructions.",
            )

        todo = _next_actionable_todo(todos)
        if todo is None:
            frontmatter["status"] = "blocked"
            frontmatter["agent_state"] = "waiting"
            frontmatter["instructions_waiting"] = True
            frontmatter["updated_at"] = _utc_now()
            body = _append_cycle_note(
                body,
                "No actionable todo found while incomplete items remain. Loop blocked.",
            )
            save_worklog(worklog_path, frontmatter, body)
            completed = sum(1 for item in todos if item.get("status") == "done")
            return WorklogLoopResult(
                status="blocked",
                completed_todos=completed,
                total_todos=len(todos),
                message="Loop blocked because dependencies prevent further progress.",
            )

        todo_id = str(todo["id"])
        todo_title = str(todo.get("title", ""))
        frontmatter["agent_state"] = "running"
        todo["status"] = "in_progress"
        frontmatter["updated_at"] = _utc_now()
        body = _append_cycle_note(body, f"Cycle {cycle}: started {todo_id} {todo_title}".strip())
        save_worklog(worklog_path, frontmatter, body)

        format_vars = {
            "todo_id": todo_id,
            "todo_title": todo_title,
            "worklog_path": str(worklog_path),
        }
        rendered_work_command = work_command.format(**format_vars)
        rendered_verify_command = verify_command.format(**format_vars)

        work_result = run_command(["bash", "-lc", rendered_work_command], cwd=cwd, check=False)
        verify_result = run_command(["bash", "-lc", rendered_verify_command], cwd=cwd, check=False)

        summary = frontmatter.setdefault("summary", {})
        if not isinstance(summary, dict):
            summary = {}
            frontmatter["summary"] = summary
        worked = summary.setdefault("what_worked_well", [])
        if not isinstance(worked, list):
            worked = []
            summary["what_worked_well"] = worked
        issues = summary.setdefault("what_caused_issues", [])
        if not isinstance(issues, list):
            issues = []
            summary["what_caused_issues"] = issues

        evidence = todo.setdefault("evidence", [])
        if not isinstance(evidence, list):
            evidence = []
            todo["evidence"] = evidence

        if work_result.returncode == 0 and verify_result.returncode == 0:
            todo["status"] = "done"
            evidence.append(
                {
                    "timestamp": _utc_now(),
                    "work_command": rendered_work_command,
                    "verify_command": rendered_verify_command,
                    "work_exit": work_result.returncode,
                    "verify_exit": verify_result.returncode,
                    "work_stdout": work_result.stdout.strip()[:500],
                    "verify_stdout": verify_result.stdout.strip()[:500],
                }
            )
            worked.append(f"{todo_id}: verification passed and evidence captured.")
            frontmatter["updated_at"] = _utc_now()
            body = _append_cycle_note(body, f"Cycle {cycle}: completed {todo_id}")
            save_worklog(worklog_path, frontmatter, body)
            continue

        todo["status"] = "blocked"
        frontmatter["status"] = "blocked"
        frontmatter["agent_state"] = "waiting"
        frontmatter["instructions_waiting"] = True
        frontmatter["updated_at"] = _utc_now()
        issues.append(
            (
                f"{todo_id}: work_exit={work_result.returncode}, "
                f"verify_exit={verify_result.returncode}."
            )
        )
        body = _append_cycle_note(
            body,
            (
                f"Cycle {cycle}: blocked {todo_id} "
                f"(work={work_result.returncode}, verify={verify_result.returncode})."
            ),
        )
        save_worklog(worklog_path, frontmatter, body)

        completed = sum(1 for item in todos if item.get("status") == "done")
        return WorklogLoopResult(
            status="blocked",
            completed_todos=completed,
            total_todos=len(todos),
            message="Todo blocked because work or verification failed.",
        )

    validate_worklog(frontmatter)
    todos_after = frontmatter.get("todos", [])
    total = len(todos_after) if isinstance(todos_after, list) else 0
    completed = (
        sum(1 for item in todos_after if isinstance(item, dict) and item.get("status") == "done")
        if isinstance(todos_after, list)
        else 0
    )
    return WorklogLoopResult(
        status="max_cycles_reached",
        completed_todos=completed,
        total_todos=total,
        message="Stopped because max_cycles was reached before completion.",
    )