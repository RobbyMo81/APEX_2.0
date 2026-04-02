from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

RULE_STORY_NOT_FOUND = "STORY_NOT_FOUND"
RULE_MISSING_FIELD = "MISSING_FIELD"
RULE_WRONG_TYPE = "WRONG_TYPE"
RULE_INVALID_STATUS = "INVALID_STATUS"
RULE_EMPTY_ARRAY = "EMPTY_ARRAY"
RULE_EMPTY_ITEM = "EMPTY_ITEM"
RULE_ALREADY_DONE = "ALREADY_DONE"
RULE_NO_PASS_RECORD = "NO_PASS_RECORD"
RULE_STATUS_PASSES_MISMATCH = "STATUS_PASSES_MISMATCH"
RULE_UNAUTHORIZED_BACKLOG_CHANGE = "UNAUTHORIZED_BACKLOG_CHANGE"
RULE_MISSING_WORKLOG_VERDICT = "MISSING_WORKLOG_VERDICT"
RULE_MISSING_REPO_EVIDENCE = "MISSING_REPO_EVIDENCE"

VALID_STATUSES = {"todo", "in_progress", "blocked", "done"}
VALID_STORY_TYPES = {"implementation", "verification", "documentation"}


def _fmt(rule_id: str, message: str) -> str:
    return f"GOVERNANCE_ERROR:{rule_id}: {message}"


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _load_prd(prd_path: Path) -> dict[str, Any]:
    return _load_json(prd_path)


def _story_map(prd: dict[str, Any]) -> dict[str, str]:
    return {
        story["id"]: story["title"]
        for story in prd.get("userStories", [])
        if isinstance(story.get("id"), str)
    }


def _find_story(prd: dict[str, Any], story_id: str) -> dict[str, Any] | None:
    for story in prd.get("userStories", []):
        if story.get("id") == story_id:
            return story
    return None


def _validate_story_fields(story: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required: dict[str, type | tuple[type, ...]] = {
        "id": str,
        "title": str,
        "status": str,
        "storyType": str,
        "implementationPlan": list,
        "acceptanceCriteria": list,
        "passes": bool,
    }

    for field, expected_type in required.items():
        if field not in story:
            errors.append(_fmt(RULE_MISSING_FIELD, f"Story {story.get('id', 'unknown')} is missing required field: {field}"))
            continue
        value = story[field]
        if field == "passes":
            if type(value) is not bool:
                errors.append(_fmt(RULE_WRONG_TYPE, f"Story {story.get('id', 'unknown')} field {field} must be boolean"))
            continue
        if not isinstance(value, expected_type):
            errors.append(_fmt(RULE_WRONG_TYPE, f"Story {story.get('id', 'unknown')} field {field} has wrong type"))

    if errors:
        return errors

    if story["status"] not in VALID_STATUSES:
        errors.append(_fmt(RULE_INVALID_STATUS, f"Story {story['id']} has invalid status: {story['status']}"))

    if story["storyType"] not in VALID_STORY_TYPES:
        errors.append(_fmt(RULE_WRONG_TYPE, f"Story {story['id']} has invalid storyType: {story['storyType']}"))

    for field in ("implementationPlan", "acceptanceCriteria"):
        values = story[field]
        if len(values) == 0:
            errors.append(_fmt(RULE_EMPTY_ARRAY, f"Story {story['id']} has empty {field}"))
            continue
        for idx, item in enumerate(values):
            if not isinstance(item, str):
                errors.append(_fmt(RULE_WRONG_TYPE, f"Story {story['id']} field {field}[{idx}] has wrong type"))
                continue
            if not item.strip():
                errors.append(_fmt(RULE_EMPTY_ITEM, f"Story {story['id']} has empty item at {field}[{idx}]"))

    return errors


def check_story(story_id: str, prd_path: Path) -> list[str]:
    prd = _load_prd(prd_path)
    story = _find_story(prd, story_id)
    if story is None:
        return [_fmt(RULE_STORY_NOT_FOUND, f"Story {story_id} does not exist in prd.json")]

    errors = _validate_story_fields(story)
    if errors:
        return errors

    if story["status"] == "done":
        return [_fmt(RULE_ALREADY_DONE, f"Story {story_id} is already marked done")]

    return []


def check_close(story_id: str, prd_path: Path, progress_path: Path, worklog_path: Path | None = None) -> list[str]:
    prd = _load_prd(prd_path)
    story = _find_story(prd, story_id)
    if story is None:
        return [_fmt(RULE_STORY_NOT_FOUND, f"Story {story_id} does not exist in prd.json")]

    errors = _validate_story_fields(story)
    if errors:
        return errors

    if (story["status"] == "done" and not story["passes"]) or (
        story["status"] != "done" and story["passes"]
    ):
        errors.append(
            _fmt(
                RULE_STATUS_PASSES_MISMATCH,
                f"Story {story_id} has inconsistent status/passes state: status={story['status']}, passes={story['passes']}",
            )
        )

    content = progress_path.read_text(encoding="utf-8")
    pattern = rf"\[\d{{4}}-\d{{2}}-\d{{2}}\] Story \[{re.escape(story_id)}\]:[^\r\n]*(?:\r?\n)STATUS: PASS \|"
    if not re.search(pattern, content):
        errors.append(_fmt(RULE_NO_PASS_RECORD, f"Story {story_id} has no PASS record in progress.txt"))

    if story["storyType"] == "verification":
        if worklog_path is None:
            errors.append(_fmt(RULE_MISSING_WORKLOG_VERDICT, f"Story {story_id} requires worklog validation"))
            return errors
        worklog_text = worklog_path.read_text(encoding="utf-8")
        verdict_patterns = [
            rf"Verdict\s+[—-]\s+{re.escape(story_id)}: DONE\b",
            rf"Verdict\s+[—-]\s+{re.escape(story_id)}: DONE \(re-verified\)",
        ]
        if not any(re.search(p, worklog_text) for p in verdict_patterns):
            errors.append(_fmt(RULE_MISSING_WORKLOG_VERDICT, f"Story {story_id} is verification-type but no approved verdict found in worklog"))
        path_pattern = r"(?<!http://)(?<!https://)(?:^|[\s`(\[])((?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+)"
        if not re.search(path_pattern, worklog_text, re.MULTILINE):
            errors.append(_fmt(RULE_MISSING_REPO_EVIDENCE, f"Story {story_id} is verification-type but no repo evidence references were found in worklog"))

    return errors


def _split_sections(text: str) -> list[str]:
    return re.split(r"(?:\n---\n|\r\n---\r\n)", text)


def check_backlog(prd_path: Path, worklog_path: Path, state_path: Path) -> list[str]:
    prd = _load_prd(prd_path)
    current = _story_map(prd)

    if not state_path.exists():
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps({"stories": current}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return []

    previous = _load_json(state_path).get("stories", {})
    changed_ids = sorted({*current.keys(), *previous.keys()} - {
        sid for sid in current.keys() & previous.keys() if current[sid] == previous[sid]
    })
    if not changed_ids:
        return []

    worklog_text = worklog_path.read_text(encoding="utf-8")
    approved = False
    for section in _split_sections(worklog_text):
        if "Supervisor approved backlog change:" not in section:
            continue
        if all(story_id in section for story_id in changed_ids):
            approved = True
            break

    if not approved:
        return [
            _fmt(
                RULE_UNAUTHORIZED_BACKLOG_CHANGE,
                f"Unauthorized backlog change detected for story IDs: {', '.join(changed_ids)}",
            )
        ]

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"stories": current}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return []


def _print_errors(errors: list[str]) -> None:
    for err in errors:
        print(err, file=sys.stderr)


def main_check_story(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="forge check-story")
    parser.add_argument("story_id")
    parser.add_argument("--prd", default="prd.json")
    args = parser.parse_args(argv)

    prd_path = Path(args.prd)
    if not prd_path.exists():
        print(f"ERROR: PRD file not found: {prd_path}", file=sys.stderr)
        return 2

    errors = check_story(args.story_id, prd_path)
    if errors:
        _print_errors(errors)
        return 1
    print("OK")
    return 0


def main_check_close(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="forge check-close")
    parser.add_argument("story_id")
    parser.add_argument("--prd", default="prd.json")
    parser.add_argument("--progress", default=".forge/progress.txt")
    parser.add_argument("--worklog", default="docs/AGENTIC_WORKLOG_LOOP.md")
    args = parser.parse_args(argv)

    prd_path = Path(args.prd)
    progress_path = Path(args.progress)
    worklog_path = Path(args.worklog)
    if not prd_path.exists():
        print(f"ERROR: PRD file not found: {prd_path}", file=sys.stderr)
        return 2
    if not progress_path.exists():
        print(f"ERROR: Progress file not found: {progress_path}", file=sys.stderr)
        return 2

    errors = check_close(args.story_id, prd_path, progress_path, worklog_path if worklog_path.exists() else None)
    if errors:
        _print_errors(errors)
        return 1
    print("OK")
    return 0


def main_check_backlog(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="forge check-backlog")
    parser.add_argument("--prd", default="prd.json")
    parser.add_argument("--worklog", default="docs/AGENTIC_WORKLOG_LOOP.md")
    parser.add_argument("--state", default=".forge/governance_state.json")
    args = parser.parse_args(argv)

    prd_path = Path(args.prd)
    worklog_path = Path(args.worklog)
    state_path = Path(args.state)
    if not prd_path.exists():
        print(f"ERROR: PRD file not found: {prd_path}", file=sys.stderr)
        return 2
    if not worklog_path.exists():
        print(f"ERROR: Worklog file not found: {worklog_path}", file=sys.stderr)
        return 2

    errors = check_backlog(prd_path, worklog_path, state_path)
    if errors:
        _print_errors(errors)
        return 1
    print("OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m forge.governance")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check-story").add_argument("story_id")
    check_close_parser = sub.add_parser("check-close")
    check_close_parser.add_argument("story_id")
    sub.add_parser("check-backlog")
    args, rest = parser.parse_known_args(argv)

    if args.command == "check-story":
        return main_check_story([args.story_id, *rest])
    if args.command == "check-close":
        return main_check_close([args.story_id, *rest])
    return main_check_backlog(rest)


if __name__ == "__main__":
    sys.exit(main())
