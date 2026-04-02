"""
Thin raw PRD validation and handoff gate (V2-044).

Validates the structure of a raw PRD markdown file before conversion and
verifies that a resulting prd.json satisfies the governed Forge entry contract.

Exit codes when used as a main:
    0 — valid
    1 — validation failure(s) found
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import NamedTuple


# Required section keywords — matched case-insensitively against H2/H3 headings
_REQUIRED_SECTIONS = [
    re.compile(r"introduction|overview|narrative|problem", re.IGNORECASE),
    re.compile(r"goal|objective|success criteria", re.IGNORECASE),
    re.compile(r"user stor|story|capabilities|requirements|functional", re.IGNORECASE),
]

# User story indicators — at least one must appear in the document
_STORY_PATTERNS = [
    re.compile(r"^###?\s+(US|Story|JAL|V2)-\d+", re.MULTILINE),
    re.compile(r"^###?\s+\w.*", re.MULTILINE),  # any H3 (stories are typically H3)
]

# Governed prd.json required story fields
_GOVERNED_STORY_FIELDS = {
    "id",
    "title",
    "priority",
    "status",
    "passes",
    "storyType",
    "description",
    "acceptanceCriteria",
    "implementationPlan",
    "technicalNotes",
    "safetyGates",
}

_VALID_STATUSES = {"todo", "in_progress", "blocked", "done"}
_VALID_STORY_TYPES = {"implementation", "verification", "documentation"}


class ValidationResult(NamedTuple):
    valid: bool
    errors: list[str]


def validate_raw_prd(prd_path: str | Path) -> ValidationResult:
    """Validate the structure of a raw PRD markdown file.

    Checks:
    - File exists and is non-empty.
    - Contains at least one H2-level section heading.
    - Contains coverage for required PRD section categories.
    - Contains at least one user story block (H3 heading).
    """
    path = Path(prd_path)
    errors: list[str] = []

    if not path.exists():
        return ValidationResult(valid=False, errors=[f"File not found: {path}"])

    content = path.read_text(encoding="utf-8").strip()
    if not content:
        return ValidationResult(valid=False, errors=[f"File is empty: {path}"])

    # Collect all H2/H3 headings
    headings = re.findall(r"^#{2,3}\s+(.+)", content, re.MULTILINE)
    heading_text = " ".join(headings)

    if not headings:
        errors.append("No H2/H3 section headings found — raw PRD must be structured with sections.")

    # Check required section coverage
    for pattern in _REQUIRED_SECTIONS:
        if not pattern.search(heading_text) and not pattern.search(content):
            errors.append(
                f"Missing required section matching: {pattern.pattern} "
                f"(expected one of: introduction, goals, user stories, or similar)"
            )

    # Check for at least one user story / story block
    has_stories = any(p.search(content) for p in _STORY_PATTERNS)
    if not has_stories:
        errors.append(
            "No user story blocks found — expected at least one H3 story section "
            "(e.g., '### US-001: Title' or '### Story: Title')."
        )

    return ValidationResult(valid=len(errors) == 0, errors=errors)


def validate_governed_prd_json(prd_json_path: str | Path) -> ValidationResult:
    """Validate that a prd.json satisfies the governed Forge story contract.

    Checks:
    - File exists and is valid JSON.
    - Contains a 'userStories' array.
    - Each story has all required governed fields.
    - Each story has valid status and storyType values.
    - No story has an empty acceptanceCriteria or implementationPlan.
    """
    path = Path(prd_json_path)
    errors: list[str] = []

    if not path.exists():
        return ValidationResult(valid=False, errors=[f"prd.json not found: {path}"])

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return ValidationResult(valid=False, errors=[f"prd.json is not valid JSON: {exc}"])

    stories = data.get("userStories", [])
    if not isinstance(stories, list) or not stories:
        errors.append("prd.json must contain a non-empty 'userStories' array.")
        return ValidationResult(valid=False, errors=errors)

    for story in stories:
        sid = story.get("id", "<unknown>")
        missing = _GOVERNED_STORY_FIELDS - set(story.keys())
        if missing:
            errors.append(f"Story {sid} missing required fields: {sorted(missing)}")
        if story.get("status") not in _VALID_STATUSES:
            errors.append(
                f"Story {sid} has invalid status '{story.get('status')}'. "
                f"Valid values: {sorted(_VALID_STATUSES)}"
            )
        if story.get("storyType") not in _VALID_STORY_TYPES:
            errors.append(
                f"Story {sid} has invalid storyType '{story.get('storyType')}'. "
                f"Valid values: {sorted(_VALID_STORY_TYPES)}"
            )
        if not story.get("acceptanceCriteria"):
            errors.append(f"Story {sid} has empty acceptanceCriteria.")
        if not story.get("implementationPlan"):
            errors.append(f"Story {sid} has empty implementationPlan.")

    return ValidationResult(valid=len(errors) == 0, errors=errors)


def validate_handoff(raw_prd_path: str | Path, prd_json_path: str | Path) -> ValidationResult:
    """Full handoff gate: validate raw PRD structure then verify governed prd.json output.

    Returns combined errors from both checks.
    """
    raw_result = validate_raw_prd(raw_prd_path)
    json_result = validate_governed_prd_json(prd_json_path)
    combined_errors = raw_result.errors + json_result.errors
    return ValidationResult(valid=len(combined_errors) == 0, errors=combined_errors)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint: python -m forge.prd_validator <raw_prd.md> [--prd-out prd.json]"""
    import argparse

    parser = argparse.ArgumentParser(
        prog="forge prd-validate",
        description="Validate a raw PRD markdown file and optionally verify governed prd.json output.",
    )
    parser.add_argument("raw_prd", help="Path to the raw PRD markdown file.")
    parser.add_argument(
        "--prd-out",
        default=None,
        help=(
            "Path to the governed prd.json output to verify against the Forge entry contract. "
            "When provided, runs the full handoff gate (raw PRD + prd.json validation). "
            "When omitted, validates raw PRD structure only."
        ),
    )
    args = parser.parse_args(argv)

    if args.prd_out:
        result = validate_handoff(args.raw_prd, args.prd_out)
        mode = "handoff gate"
    else:
        result = validate_raw_prd(args.raw_prd)
        mode = "raw PRD structure"

    if result.valid:
        print(f"OK — {mode} validation passed: {args.raw_prd}")
        return 0

    print(f"FAIL — {mode} validation failed: {args.raw_prd}", file=sys.stderr)
    for error in result.errors:
        print(f"  - {error}", file=sys.stderr)
    return 1
