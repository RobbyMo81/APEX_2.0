from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
PRD_PATH = REPO_ROOT / "prd.json"
WORKLOG_PATH = REPO_ROOT / "docs" / "AGENTIC_WORKLOG_LOOP.md"
STATE_PATH = REPO_ROOT / ".forge" / "phase6_done_monitor_state.json"
LOGICAL_PROGRESS_PATHS = [
    REPO_ROOT / "progress.txt",
    REPO_ROOT / ".forge" / "progress.txt",
]
MONITORED_STORIES = tuple(f"V2-0{i}" for i in range(34, 42))


@dataclass(frozen=True, slots=True)
class VerificationResult:
    story_id: str
    title: str
    justified: bool
    summary: str
    evidence: list[str]
    issues: list[str]


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _story_fingerprint(story: dict[str, Any]) -> str:
    encoded = json.dumps(story, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _worklog_text() -> str:
    return WORKLOG_PATH.read_text(encoding="utf-8") if WORKLOG_PATH.exists() else ""


def _progress_text() -> str:
    chunks: list[str] = []
    for path in LOGICAL_PROGRESS_PATHS:
        if path.exists():
            chunks.append(path.read_text(encoding="utf-8"))
    return "\n".join(chunks)


def _has_progress_pass(story_id: str) -> bool:
    pattern = rf"\[\d{{4}}-\d{{2}}-\d{{2}}(?: [^\]]+)?\]\s+(?:Story\s+\[)?{re.escape(story_id)}(?:\])?"
    return re.search(pattern, _progress_text()) is not None


def _has_worklog_verdict(story_id: str) -> bool:
    text = _worklog_text()
    return re.search(rf"Verdict\s+[—-]\s+{re.escape(story_id)}:\s+DONE\b", text) is not None


def _file_exists(relative_path: str) -> bool:
    return (REPO_ROOT / relative_path).exists()


def _file_contains(relative_path: str, pattern: str) -> bool:
    path = REPO_ROOT / relative_path
    if not path.exists():
        return False
    return re.search(pattern, path.read_text(encoding="utf-8"), re.MULTILINE) is not None


def _run_pytest(args: list[str]) -> tuple[bool, str]:
    python_bin = REPO_ROOT / ".venv" / "bin" / "python"
    executable = str(python_bin) if python_bin.exists() else sys.executable
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(REPO_ROOT) if not existing else f"{REPO_ROOT}:{existing}"
    result = subprocess.run(
        [executable, "-m", "pytest", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    output = (result.stdout + "\n" + result.stderr).strip()
    tail = "\n".join(output.splitlines()[-12:]) if output else "(no pytest output)"
    return result.returncode == 0, tail


def _base_story_checks(story_id: str) -> tuple[list[str], list[str]]:
    evidence: list[str] = []
    issues: list[str] = []
    if _has_progress_pass(story_id):
        evidence.append(f"PASS record found in progress logs for {story_id}")
    else:
        issues.append(f"No PASS record found in progress logs for {story_id}")
    if _has_worklog_verdict(story_id):
        evidence.append(f"Worklog verdict already exists for {story_id}")
    else:
        issues.append(f"No explicit worklog DONE verdict found for {story_id}")
    return evidence, issues


def _verify_v2034(story: dict[str, Any]) -> VerificationResult:
    evidence, issues = _base_story_checks(story["id"])
    if _file_exists("forge/memory.py"):
        evidence.append("forge/memory.py exists")
    else:
        issues.append("forge/memory.py is missing")
    if _file_contains("forge/runner.py", r"init_memory\("):
        evidence.append("forge/runner.py calls init_memory()")
    else:
        issues.append("forge/runner.py does not call init_memory()")
    passed, output = _run_pytest(["tests/python/test_memory.py", "-q"])
    if passed:
        evidence.append("tests/python/test_memory.py passed")
    else:
        issues.append(f"tests/python/test_memory.py failed:\n{output}")
    justified = passed and not issues
    summary = "done is justified" if justified else "done is not cleanly justified"
    return VerificationResult(story["id"], story["title"], justified, summary, evidence, issues)


def _verify_v2035(story: dict[str, Any]) -> VerificationResult:
    evidence, issues = _base_story_checks(story["id"])
    if _file_exists("forge/sidecars.py"):
        evidence.append("forge/sidecars.py exists")
    else:
        issues.append("forge/sidecars.py is missing")
    if _file_contains("forge/runner.py", r"SidecarOrchestrator"):
        evidence.append("forge/runner.py integrates SidecarOrchestrator")
    else:
        issues.append("forge/runner.py does not integrate SidecarOrchestrator")
    passed, output = _run_pytest(["tests/python/test_sidecars.py", "-q"])
    if passed:
        evidence.append("tests/python/test_sidecars.py passed")
    else:
        issues.append(f"tests/python/test_sidecars.py failed:\n{output}")
    justified = passed and not issues
    summary = "done is justified" if justified else "done is not cleanly justified"
    return VerificationResult(story["id"], story["title"], justified, summary, evidence, issues)


def _verify_v2036(story: dict[str, Any]) -> VerificationResult:
    evidence, issues = _base_story_checks(story["id"])
    if _file_contains("forge/runner.py", r"COMPLETE_MARKER"):
        evidence.append("forge/runner.py defines COMPLETE_MARKER")
    else:
        issues.append("forge/runner.py is missing COMPLETE_MARKER")
    if _file_contains("forge/runner.py", r"run_final_lint\("):
        evidence.append("forge/runner.py runs final lint before mission completion")
    else:
        issues.append("forge/runner.py does not run final lint in the completion path")
    pytest_args = [
        "tests/python/test_runner.py",
        "-q",
        "-k",
        (
            "test_check_all_complete_true_when_all_pass or "
            "test_check_all_complete_false_when_some_unpassed or "
            "test_check_all_complete_false_when_partially_done or "
            "test_run_once_emits_complete_marker_when_lint_passes or "
            "test_run_once_returns_lint_failed_when_lint_fails or "
            "test_run_main_complete_on_zero_remaining_stories or "
            "test_run_main_complete_on_no_selectable_story or "
            "test_run_main_paused_on_max_iterations_exhaustion"
        ),
    ]
    passed, output = _run_pytest(pytest_args)
    if passed:
        evidence.append("focused V2-036 runner tests passed")
    else:
        issues.append(f"focused V2-036 runner tests failed:\n{output}")
    justified = passed and not issues
    summary = "done is justified" if justified else "done is not cleanly justified"
    return VerificationResult(story["id"], story["title"], justified, summary, evidence, issues)


def _verify_v2037(story: dict[str, Any]) -> VerificationResult:
    evidence, issues = _base_story_checks(story["id"])
    if _file_exists("forge/git.py"):
        evidence.append("forge/git.py exists")
    else:
        issues.append("forge/git.py is missing")
    if _file_contains("forge/runner.py", r"ensure_branch\("):
        evidence.append("forge/runner.py calls ensure_branch()")
    else:
        issues.append("forge/runner.py does not call ensure_branch()")
    if _file_contains("forge/runner.py", r"commit_story_pass\("):
        evidence.append("forge/runner.py calls commit_story_pass()")
    else:
        issues.append("forge/runner.py does not call commit_story_pass()")
    passed, output = _run_pytest(["tests/python/test_git.py", "-q"])
    if passed:
        evidence.append("tests/python/test_git.py passed")
    else:
        issues.append(f"tests/python/test_git.py failed:\n{output}")
    justified = passed and not issues
    summary = "done is justified" if justified else "done is not cleanly justified"
    return VerificationResult(story["id"], story["title"], justified, summary, evidence, issues)


def _verify_v2038(story: dict[str, Any]) -> VerificationResult:
    evidence, issues = _base_story_checks(story["id"])
    pytest_args = [
        "tests/python/test_runner.py",
        "-q",
        "-k",
        (
            "test_backend_timeout_emits_backend_timeout_audit or "
            "test_backend_error_emits_backend_error_audit or "
            "test_mark_story_failed_emits_story_fail_audit_without_mutating_passes or "
            "test_run_main_logs_retry_warning_on_failed_iteration or "
            "test_run_main_paused_on_max_iterations_exits_nonzero_equiv"
        ),
    ]
    passed, output = _run_pytest(pytest_args)
    if passed:
        evidence.append("focused V2-038 runner tests passed")
    else:
        issues.append(f"focused V2-038 runner tests failed:\n{output}")
    justified = passed and not issues
    summary = "done is justified" if justified else "done is not cleanly justified"
    return VerificationResult(story["id"], story["title"], justified, summary, evidence, issues)


def _verify_v2039(story: dict[str, Any]) -> VerificationResult:
    evidence, issues = _base_story_checks(story["id"])
    if _file_contains("forge/runner.py", r"V2-039 will implement the full contract"):
        issues.append("forge/runner.py still contains the V2-039 stub placeholder")
    else:
        evidence.append("forge/runner.py no longer contains the V2-039 stub placeholder")
    if _file_contains("forge/runner.py", r"No-op placeholder"):
        issues.append("trigger_sic() is still a no-op placeholder")
    if _file_exists("tests/python/test_sic.py"):
        passed, output = _run_pytest(["tests/python/test_sic.py", "-q"])
        if passed:
            evidence.append("tests/python/test_sic.py passed")
        else:
            issues.append(f"tests/python/test_sic.py failed:\n{output}")
    else:
        issues.append("No focused SIC/GitHub-cycle test surface found (expected tests/python/test_sic.py)")
    justified = not issues
    summary = "done is justified" if justified else "done is not justified"
    return VerificationResult(story["id"], story["title"], justified, summary, evidence, issues)


def _verify_v2040(story: dict[str, Any]) -> VerificationResult:
    evidence, issues = _base_story_checks(story["id"])
    cli_text = (REPO_ROOT / "forge" / "cli.py").read_text(encoding="utf-8")
    for command_name in ("generate-gates", "commit-rules", "config"):
        if f'"{command_name}"' in cli_text or f"'{command_name}'" in cli_text:
            evidence.append(f"Python CLI exposes {command_name}")
        else:
            issues.append(f"Python CLI does not expose {command_name}")
    justified = not issues
    summary = "done is justified" if justified else "done is not justified"
    return VerificationResult(story["id"], story["title"], justified, summary, evidence, issues)


def _verify_v2041(story: dict[str, Any]) -> VerificationResult:
    evidence, issues = _base_story_checks(story["id"])
    prd = _load_json(PRD_PATH)
    phase6 = {
        item["id"]: item
        for item in prd.get("userStories", [])
        if isinstance(item.get("id"), str) and "V2-033" <= item["id"] <= "V2-040"
    }
    incomplete = sorted(
        story_id
        for story_id, item in phase6.items()
        if item.get("status") != "done" or item.get("passes") is not True
    )
    if incomplete:
        issues.append(f"Phase 6 prerequisite stories are not all done/passed: {', '.join(incomplete)}")
    else:
        evidence.append("All prerequisite Phase 6 implementation stories are done/passed")
    readme_text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    roadmap_text = (REPO_ROOT / "docs" / "FORGE_PYTHON_MIGRATION_ROADMAP.md").read_text(encoding="utf-8")
    post_phase6_text = (REPO_ROOT / "docs" / "FORGE_POST_PHASE6_PLAN.md").read_text(encoding="utf-8")
    if "Phase 6" in readme_text and "Phase 6" in roadmap_text and "Phase 6" in post_phase6_text:
        evidence.append("Phase 6 is referenced in all three closure documents")
    else:
        issues.append("One or more closure documents do not reference Phase 6 state")
    justified = not issues
    summary = "done is justified" if justified else "done is not justified"
    return VerificationResult(story["id"], story["title"], justified, summary, evidence, issues)


VERIFY_MAP = {
    "V2-034": _verify_v2034,
    "V2-035": _verify_v2035,
    "V2-036": _verify_v2036,
    "V2-037": _verify_v2037,
    "V2-038": _verify_v2038,
    "V2-039": _verify_v2039,
    "V2-040": _verify_v2040,
    "V2-041": _verify_v2041,
}


def _append_worklog(results: list[VerificationResult]) -> None:
    if not results:
        return
    timestamp = _utc_now()
    lines = [f"\n### {timestamp} — Phase 6 Done-Story Monitor\n"]
    for result in results:
        lines.append(f"- `{result.story_id}` `{result.title}`: {result.summary}.")
        if result.evidence:
            lines.append("  Evidence:")
            for item in result.evidence:
                lines.append(f"  - {item}")
        if result.issues:
            lines.append("  Issues:")
            for item in result.issues:
                lines.append(f"  - {item}")
    with WORKLOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def run_monitor() -> int:
    prd = _load_json(PRD_PATH)
    state = _load_json(STATE_PATH) if STATE_PATH.exists() else {"stories": {}}
    seen: dict[str, Any] = state.setdefault("stories", {})
    pending_results: list[VerificationResult] = []

    for story in prd.get("userStories", []):
        story_id = story.get("id")
        if story_id not in MONITORED_STORIES:
            continue
        if story.get("status") != "done":
            continue
        fingerprint = _story_fingerprint(story)
        previous = seen.get(story_id, {})
        if previous.get("fingerprint") == fingerprint and previous.get("status") == "done":
            continue
        verifier = VERIFY_MAP.get(story_id)
        if verifier is None:
            continue
        result = verifier(story)
        pending_results.append(result)
        seen[story_id] = {
            "status": "done",
            "fingerprint": fingerprint,
            "verified_at": _utc_now(),
            "justified": result.justified,
            "summary": result.summary,
        }

    if pending_results:
        _append_worklog(pending_results)
    _save_json(STATE_PATH, state)
    return 0


def main() -> int:
    try:
        return run_monitor()
    except Exception as exc:  # pragma: no cover - cron-safe fallback
        sys.stderr.write(f"phase6_done_monitor failed: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
