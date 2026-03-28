"""
Vashion - dreaming.py

Mandatory nightly self-reflection and SIC generation task.

Purpose
-------
Runs the non-negotiable 23:00 local self-reflection cycle for Vashion.
This script is designed as the orchestration layer for the nightly ritual:

1. Rehydrate the Self review workspace
2. Freeze the day
3. Collect relevant daily signals
4. Perform self-reflection
5. Generate SIC (Self-Inspection Cycle) artifact
6. Emit memory / lesson / self-update / improvement candidates
7. Persist outputs
8. Prepare next-day operational state

Notes
-----
- This file is intentionally implementation-oriented but uses simple local file
  interfaces so it can be adapted into the larger Vashion runtime.
- The script does NOT directly mutate Self artifacts. It only proposes updates.
- The script assumes local filesystem-backed data for v1.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
import json
import hashlib
import os
import subprocess

try:
    import yaml  # type: ignore
except ImportError as exc:  # pragma: no cover
    raise RuntimeError(
        "PyYAML is required for dreaming.py. Install with: pip install pyyaml"
    ) from exc


# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

BASE_DIR = Path(os.environ.get("VASHION_HOME", Path.home() / ".vashion"))
TOKEN_LEDGER_DIR = BASE_DIR / "token_ledger"
STM_DIR = BASE_DIR / "short_term_memory"
LTM_DIR = BASE_DIR / "long_term_memory"
SELF_DIR = BASE_DIR / "self"
SIC_DIR = LTM_DIR / "SIC"
LOG_DIR = BASE_DIR / "logs"
RUNTIME_DIR = BASE_DIR / "runtime"

SELF_PAGE_ID = "ctx-self-001"
SELF_PAGE_PATH = TOKEN_LEDGER_DIR / f"{SELF_PAGE_ID}.yaml"
MANIFEST_PATH = RUNTIME_DIR / "context_manifest.yaml"
DREAMING_STATE_PATH = RUNTIME_DIR / "dreaming_state.yaml"

DEFAULT_LOCAL_TIME = "23:00"
DEFAULT_GIT_BRANCH = os.environ.get("VASHION_GIT_BRANCH", "main")
DEFAULT_GIT_REMOTE = os.environ.get("VASHION_GIT_REMOTE", "origin")
DREAMING_LOCK_PATH = RUNTIME_DIR / "dreaming.lock"
MISSED_CYCLE_PATH = RUNTIME_DIR / "missed_dreaming_cycles.yaml"
ERROR_STATE_PATH = RUNTIME_DIR / "dreaming_error_state.yaml"


# -----------------------------------------------------------------------------
# Data models
# -----------------------------------------------------------------------------

@dataclass
class ContextPage:
    page_id: str
    title: str
    context_type: str
    status: str
    active_context_summary: str = ""
    open_loops: List[str] = field(default_factory=list)
    current_objectives: List[str] = field(default_factory=list)
    transcript_stream: List[Dict[str, Any]] = field(default_factory=list)
    short_term_refs: List[str] = field(default_factory=list)
    long_term_story_refs: List[str] = field(default_factory=list)
    specialization: Dict[str, Any] = field(default_factory=dict)
    updated_at: Optional[str] = None


@dataclass
class DailySignalBundle:
    run_date: str
    home_summary: str = ""
    active_page_summaries: List[Dict[str, Any]] = field(default_factory=list)
    short_term_entries: List[Dict[str, Any]] = field(default_factory=list)
    self_state: Dict[str, str] = field(default_factory=dict)
    execution_notes: List[Dict[str, Any]] = field(default_factory=list)
    approvals: List[Dict[str, Any]] = field(default_factory=list)
    failures: List[Dict[str, Any]] = field(default_factory=list)
    communications_summary: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class SICArtifact:
    sic_id: str
    date: str
    source_cycle: str
    day_summary: str
    key_interactions: List[Dict[str, Any]]
    successes: List[str]
    failures_or_friction: List[str]
    patterns_observed: List[str]
    self_alignment: Dict[str, str]
    drift_indicators: List[str]
    lessons_learned: List[str]
    memory_candidates: List[Dict[str, Any]]
    self_update_candidates: List[Dict[str, Any]]
    improvement_candidates: List[Dict[str, Any]]
    carry_forward: List[str]
    provenance: Dict[str, Any]


@dataclass
class DreamingRunResult:
    status: str
    run_date: str
    sic_path: Optional[str] = None
    error: Optional[str] = None
    recovery_action: Optional[str] = None
    git_push: Optional[Dict[str, Any]] = None


# -----------------------------------------------------------------------------
# Filesystem helpers
# -----------------------------------------------------------------------------


def ensure_directories() -> None:
    for path in [
        TOKEN_LEDGER_DIR,
        STM_DIR,
        LTM_DIR,
        SELF_DIR,
        SIC_DIR,
        LOG_DIR,
        RUNTIME_DIR,
    ]:
        path.mkdir(parents=True, exist_ok=True)



def load_yaml(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return default if data is None else data
    except Exception:
        _append_runtime_error(
            stage="load_yaml",
            message=f"Failed to load YAML from {path}",
            details=traceback.format_exc(),
            severity="warning",
        )
        return default



def write_yaml(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)
    tmp_path.replace(path)



def load_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")



def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.replace(path)


# -----------------------------------------------------------------------------
# Runtime guardrails and recovery
# -----------------------------------------------------------------------------


def acquire_dreaming_lock() -> None:
    if DREAMING_LOCK_PATH.exists():
        raise RuntimeError(
            "dreaming.py appears to already be running or a stale lock exists."
        )
    write_yaml(
        DREAMING_LOCK_PATH,
        {
            "pid": os.getpid(),
            "started_at": _now_iso(),
        },
    )



def release_dreaming_lock() -> None:
    if DREAMING_LOCK_PATH.exists():
        DREAMING_LOCK_PATH.unlink()



def mark_missed_cycle(run_date: str, reason: str) -> None:
    rows = load_yaml(MISSED_CYCLE_PATH, [])
    if not isinstance(rows, list):
        rows = []
    rows.append(
        {
            "run_date": run_date,
            "reason": reason,
            "recorded_at": _now_iso(),
            "status": "pending_recovery",
        }
    )
    write_yaml(MISSED_CYCLE_PATH, rows)



def resolve_missed_cycle(run_date: str) -> None:
    rows = load_yaml(MISSED_CYCLE_PATH, [])
    if not isinstance(rows, list):
        return
    changed = False
    for row in rows:
        if row.get("run_date") == run_date and row.get("status") == "pending_recovery":
            row["status"] = "recovered"
            row["recovered_at"] = _now_iso()
            changed = True
    if changed:
        write_yaml(MISSED_CYCLE_PATH, rows)



def recover_missed_cycles() -> List[str]:
    rows = load_yaml(MISSED_CYCLE_PATH, [])
    if not isinstance(rows, list):
        return []
    return [
        row["run_date"]
        for row in rows
        if row.get("status") == "pending_recovery" and row.get("run_date")
    ]



def _append_runtime_error(stage: str, message: str, details: str, severity: str = "error") -> None:
    payload = load_yaml(ERROR_STATE_PATH, [])
    if not isinstance(payload, list):
        payload = []
    payload.append(
        {
            "ts": _now_iso(),
            "stage": stage,
            "message": message,
            "details": details,
            "severity": severity,
        }
    )
    write_yaml(ERROR_STATE_PATH, payload)


# -----------------------------------------------------------------------------
# Context page operations
# -----------------------------------------------------------------------------


def ensure_self_page() -> ContextPage:
    existing = load_yaml(SELF_PAGE_PATH, None)
    if existing:
        return ContextPage(
            page_id=existing.get("page_id", SELF_PAGE_ID),
            title=existing.get("title", "Self"),
            context_type=existing.get("context_type", "identity"),
            status=existing.get("status", "warm"),
            active_context_summary=existing.get("active_context_summary", ""),
            open_loops=existing.get("open_loops", []),
            current_objectives=existing.get("current_objectives", []),
            transcript_stream=existing.get("transcript_stream", []),
            short_term_refs=existing.get("short_term_refs", []),
            long_term_story_refs=existing.get("long_term_story_refs", []),
            specialization=existing.get("specialization", {}),
            updated_at=existing.get("updated_at"),
        )

    page = ContextPage(
        page_id=SELF_PAGE_ID,
        title="Self",
        context_type="identity",
        status="warm",
        active_context_summary="Nightly self-reflection workspace for Vashion.",
        specialization={
            "domain": "selfhood",
            "persona_mode": "introspective",
            "task_mode": "identity_review",
            "risk_posture": "cautious",
            "memory_bias": "pattern_heavy",
        },
        updated_at=_now_iso(),
    )
    persist_context_page(page)
    return page



def persist_context_page(page: ContextPage) -> None:
    payload = asdict(page)
    payload["updated_at"] = _now_iso()
    write_yaml(SELF_PAGE_PATH if page.page_id == SELF_PAGE_ID else TOKEN_LEDGER_DIR / f"{page.page_id}.yaml", payload)



def rehydrate_self_page() -> ContextPage:
    page = ensure_self_page()
    page.status = "active"
    page.updated_at = _now_iso()
    persist_context_page(page)
    return page



def warm_self_page(page: ContextPage) -> None:
    page.status = "warm"
    page.updated_at = _now_iso()
    persist_context_page(page)


# -----------------------------------------------------------------------------
# Data collection
# -----------------------------------------------------------------------------


def collect_daily_signals(run_date: str) -> DailySignalBundle:
    bundle = DailySignalBundle(run_date=run_date)

    bundle.self_state = {
        "Behavior.md": load_text(SELF_DIR / "Behavior.md"),
        "Soul.md": load_text(SELF_DIR / "Soul.md"),
        "Senses.md": load_text(SELF_DIR / "Senses.md"),
    }

    # Context manifest is optional in v1. If absent, scan Token Ledger files.
    manifest = load_yaml(MANIFEST_PATH, {})
    page_ids = manifest.get("pages", []) if isinstance(manifest, dict) else []
    if not page_ids:
        page_ids = [p.stem for p in TOKEN_LEDGER_DIR.glob("*.yaml")]

    for page_id in page_ids:
        path = TOKEN_LEDGER_DIR / f"{page_id}.yaml"
        page = load_yaml(path, {})
        if not isinstance(page, dict):
            continue
        summary = {
            "page_id": page.get("page_id", page_id),
            "title": page.get("title", page_id),
            "status": page.get("status", "unknown"),
            "active_context_summary": page.get("active_context_summary", ""),
            "open_loops": page.get("open_loops", []),
            "current_objectives": page.get("current_objectives", []),
        }
        if page.get("title") == "Home":
            bundle.home_summary = page.get("active_context_summary", "")
        bundle.active_page_summaries.append(summary)

    # Collect short-term memory from today's folder if present.
    today_dir = STM_DIR / run_date
    if today_dir.exists():
        for file_path in sorted(today_dir.glob("*.md")):
            parsed = parse_yaml_markdown(file_path)
            bundle.short_term_entries.append(
                {
                    "file": str(file_path),
                    "title": file_path.stem,
                    "data": parsed,
                }
            )

    # Optional JSON/YAML runtime logs.
    bundle.execution_notes = _load_optional_records(RUNTIME_DIR / "execution_notes.yaml")
    bundle.approvals = _load_optional_records(RUNTIME_DIR / "approvals.yaml")
    bundle.failures = _load_optional_records(RUNTIME_DIR / "failures.yaml")
    bundle.communications_summary = _load_optional_records(RUNTIME_DIR / "communications_summary.yaml")

    return bundle



def _load_optional_records(path: Path) -> List[Dict[str, Any]]:
    data = load_yaml(path, [])
    return data if isinstance(data, list) else []


# -----------------------------------------------------------------------------
# Reflection and SIC generation
# -----------------------------------------------------------------------------


def perform_self_reflection(bundle: DailySignalBundle) -> SICArtifact:
    interactions = _derive_key_interactions(bundle)
    successes = _derive_successes(bundle)
    failures = _derive_failures(bundle)
    patterns = _derive_patterns(bundle)
    alignment = _derive_self_alignment(bundle)
    drift = _derive_drift_indicators(bundle)
    lessons = _derive_lessons(bundle, patterns, failures)
    memory_candidates = _derive_memory_candidates(bundle, lessons)
    self_update_candidates = _derive_self_update_candidates(bundle, patterns, drift)
    improvement_candidates = _derive_improvement_candidates(bundle, failures, drift)
    carry_forward = _derive_carry_forward(bundle)

    sic = SICArtifact(
        sic_id=f"sic-{bundle.run_date}",
        date=bundle.run_date,
        source_cycle="nightly_self_reflection",
        day_summary=_derive_day_summary(bundle, patterns, successes, failures),
        key_interactions=interactions,
        successes=successes,
        failures_or_friction=failures,
        patterns_observed=patterns,
        self_alignment=alignment,
        drift_indicators=drift,
        lessons_learned=lessons,
        memory_candidates=memory_candidates,
        self_update_candidates=self_update_candidates,
        improvement_candidates=improvement_candidates,
        carry_forward=carry_forward,
        provenance={
            "generated_at": _now_iso(),
            "source_run_date": bundle.run_date,
            "signal_counts": {
                "pages": len(bundle.active_page_summaries),
                "stm_files": len(bundle.short_term_entries),
                "approvals": len(bundle.approvals),
                "failures": len(bundle.failures),
                "communications": len(bundle.communications_summary),
            },
            "content_hash": _bundle_hash(bundle),
        },
    )
    return sic



def _derive_day_summary(
    bundle: DailySignalBundle,
    patterns: List[str],
    successes: List[str],
    failures: List[str],
) -> str:
    parts: List[str] = []
    if bundle.home_summary:
        parts.append(f"Home context: {bundle.home_summary}")
    if patterns:
        parts.append(f"Patterns observed: {patterns[0]}")
    if successes:
        parts.append(f"Primary success: {successes[0]}")
    if failures:
        parts.append(f"Primary friction: {failures[0]}")
    return " ".join(parts) if parts else "Daily self-reflection completed with limited source material."



def _derive_key_interactions(bundle: DailySignalBundle) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for page in bundle.active_page_summaries:
        if page.get("active_context_summary"):
            items.append(
                {
                    "topic": page.get("title", "unknown"),
                    "significance": "high" if page.get("status") == "active" else "medium",
                    "summary": page.get("active_context_summary", ""),
                }
            )
    return items[:10]



def _derive_successes(bundle: DailySignalBundle) -> List[str]:
    results: List[str] = []
    if bundle.short_term_entries:
        results.append("Short-term memory captured daily operational state.")
    if bundle.active_page_summaries:
        results.append("Context continuity was preserved across Token Ledger pages.")
    if bundle.approvals:
        results.append("Approval activity was recorded and available for review.")
    return results or ["Nightly cycle completed without missing core inputs."]



def _derive_failures(bundle: DailySignalBundle) -> List[str]:
    if bundle.failures:
        return [
            item.get("summary", json.dumps(item, ensure_ascii=False))
            for item in bundle.failures[:10]
        ]
    return []



def _derive_patterns(bundle: DailySignalBundle) -> List[str]:
    patterns: List[str] = []
    if len(bundle.active_page_summaries) > 1:
        patterns.append("Work spanned multiple context pages, suggesting cross-domain continuity.")
    if bundle.communications_summary:
        patterns.append("External communication signals contributed to daily context formation.")
    if bundle.short_term_entries:
        patterns.append("Operational memory remains the main source for nightly consolidation.")
    return patterns or ["No strong recurring pattern identified from available inputs."]



def _derive_self_alignment(bundle: DailySignalBundle) -> Dict[str, str]:
    return {
        "behavior_alignment": "review_required" if not bundle.self_state.get("Behavior.md") else "present",
        "soul_alignment": "review_required" if not bundle.self_state.get("Soul.md") else "present",
        "senses_alignment": "review_required" if not bundle.self_state.get("Senses.md") else "present",
    }



def _derive_drift_indicators(bundle: DailySignalBundle) -> List[str]:
    drift: List[str] = []
    if len(bundle.active_page_summaries) > 5:
        drift.append("Potential context sprawl detected across many active pages.")
    if len(bundle.failures) > 3:
        drift.append("Repeated friction suggests behavioral or process adjustment may be needed.")
    return drift



def _derive_lessons(
    bundle: DailySignalBundle,
    patterns: List[str],
    failures: List[str],
) -> List[str]:
    lessons = []
    if patterns:
        lessons.append(patterns[0])
    if failures:
        lessons.append("Friction points should be converted into bounded improvement proposals.")
    return lessons or ["Preserve continuity and summarize before compressing context."]



def _derive_memory_candidates(
    bundle: DailySignalBundle,
    lessons: List[str],
) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for lesson in lessons[:3]:
        candidates.append(
            {
                "candidate_type": "lesson",
                "index": "Lessons Learned",
                "title": lesson[:80],
                "summary": lesson,
            }
        )
    return candidates



def _derive_self_update_candidates(
    bundle: DailySignalBundle,
    patterns: List[str],
    drift: List[str],
) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for item in patterns[:2]:
        candidates.append(
            {
                "target": "Senses.md",
                "reason": item,
                "proposed_change": "Increase salience of repeated contextual signals.",
            }
        )
    for item in drift[:2]:
        candidates.append(
            {
                "target": "Behavior.md",
                "reason": item,
                "proposed_change": "Tighten page-boundary discipline and reduce context contamination.",
            }
        )
    return candidates



def _derive_improvement_candidates(
    bundle: DailySignalBundle,
    failures: List[str],
    drift: List[str],
) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for item in failures[:3]:
        candidates.append(
            {
                "scope": "process",
                "risk": "bounded",
                "summary": item,
                "next_step": "Stage validation-oriented improvement proposal.",
            }
        )
    for item in drift[:2]:
        candidates.append(
            {
                "scope": "governance",
                "risk": "bounded",
                "summary": item,
                "next_step": "Review coordinator and context routing policy.",
            }
        )
    return candidates



def _derive_carry_forward(bundle: DailySignalBundle) -> List[str]:
    loops: List[str] = []
    for page in bundle.active_page_summaries:
        loops.extend(page.get("open_loops", []))
    return loops[:10]


# -----------------------------------------------------------------------------
# Persistence of nightly outputs
# -----------------------------------------------------------------------------


def persist_sic(sic: SICArtifact) -> Path:
    path = SIC_DIR / f"{sic.sic_id}.yaml"
    write_yaml(path, asdict(sic))
    return path



def append_sic_to_self_page(page: ContextPage, sic: SICArtifact) -> None:
    page.active_context_summary = (
        f"Last nightly self-reflection completed for {sic.date}. "
        f"SIC generated with {len(sic.lessons_learned)} lesson(s) and "
        f"{len(sic.self_update_candidates)} self-update candidate(s)."
    )
    page.open_loops = sic.carry_forward
    page.current_objectives = [
        "Review self-update candidates",
        "Review improvement candidates",
    ]
    page.long_term_story_refs = list({*page.long_term_story_refs, f"sic://{sic.sic_id}"})
    page.transcript_stream.append(
        {
            "ts": _now_iso(),
            "role": "system",
            "summary": f"Nightly self-reflection completed. SIC stored as {sic.sic_id}.",
        }
    )
    persist_context_page(page)



def emit_candidate_files(sic: SICArtifact) -> None:
    candidate_dir = RUNTIME_DIR / "nightly_candidates" / sic.date
    candidate_dir.mkdir(parents=True, exist_ok=True)
    write_yaml(candidate_dir / "memory_candidates.yaml", sic.memory_candidates)
    write_yaml(candidate_dir / "self_update_candidates.yaml", sic.self_update_candidates)
    write_yaml(candidate_dir / "improvement_candidates.yaml", sic.improvement_candidates)



def freeze_day(run_date: str) -> None:
    state = load_yaml(DREAMING_STATE_PATH, {})
    state["last_frozen_date"] = run_date
    state["last_freeze_ts"] = _now_iso()
    write_yaml(DREAMING_STATE_PATH, state)



def seed_next_day_short_term_memory(run_date: str) -> None:
    next_date = (
        datetime.strptime(run_date, "%Y-%m-%d") + timedelta(days=1)
    ).strftime("%Y-%m-%d")
    next_dir = STM_DIR / next_date
    next_dir.mkdir(parents=True, exist_ok=True)

    default_files = {
        "Active_Goals.md": {
            "title": "Active_Goals",
            "date": next_date,
            "entries": [],
        },
        "Open_Loops.md": {
            "title": "Open_Loops",
            "date": next_date,
            "entries": [],
        },
        "Daily_Observations.md": {
            "title": "Daily_Observations",
            "date": next_date,
            "entries": [],
        },
        "Pending_Approvals.md": {
            "title": "Pending_Approvals",
            "date": next_date,
            "entries": [],
        },
        "Reflection.md": {
            "title": "Reflection",
            "date": next_date,
            "entries": [],
        },
    }

    for filename, data in default_files.items():
        path = next_dir / filename
        if not path.exists():
            write_yaml_markdown(path, data)


# -----------------------------------------------------------------------------
# Parsing helpers
# -----------------------------------------------------------------------------


def parse_yaml_markdown(path: Path) -> Dict[str, Any]:
    text = load_text(path).strip()
    if not text:
        return {}

    # v1 format: treat as pure YAML body or frontmatter-style YAML.
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            body = parts[1]
            return yaml.safe_load(body) or {}
    return yaml.safe_load(text) or {}



def write_yaml_markdown(path: Path, data: Dict[str, Any]) -> None:
    content = "---\n" + yaml.safe_dump(data, sort_keys=False, allow_unicode=True) + "---\n"
    write_text(path, content)


# -----------------------------------------------------------------------------
# GitHub push finalization
# -----------------------------------------------------------------------------


def finalize_with_github_push(run_date: str, sic_path: Path) -> Dict[str, Any]:
    """
    Final step of the dreaming cycle.

    The nightly cycle is not considered complete until the current repository state,
    including the newly generated SIC and candidate artifacts, has been committed and
    pushed to the configured remote.

    Assumptions:
    - dreaming.py is executed from within a Git worktree for the Vashion repo
    - remote auth is already configured on the host
    - branch and remote may be overridden with environment variables
    """
    repo_root = _detect_git_repo_root()
    if repo_root is None:
        raise RuntimeError("Dreaming cycle cannot finalize: no Git repository detected.")

    branch = DEFAULT_GIT_BRANCH
    remote = DEFAULT_GIT_REMOTE
    commit_message = f"dreaming: nightly SIC {run_date}"

    _run_git(["status", "--short"], repo_root)
    _run_git(["add", "."], repo_root)

    status_after_add = _run_git(["status", "--short"], repo_root).strip()
    if not status_after_add:
        return {
            "repo_root": str(repo_root),
            "remote": remote,
            "branch": branch,
            "commit": None,
            "pushed": False,
            "reason": "no_changes",
        }

    commit_proc = subprocess.run(
        ["git", "commit", "-m", commit_message],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    if commit_proc.returncode != 0:
        stderr = (commit_proc.stderr or "").strip()
        stdout = (commit_proc.stdout or "").strip()
        # Allow clean handling when there is nothing new to commit.
        if "nothing to commit" not in f"{stdout}
{stderr}".lower():
            raise RuntimeError(
                f"Git commit failed: {stdout or stderr or 'unknown git commit error'}"
            )

    push_proc = subprocess.run(
        ["git", "push", remote, branch],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    if push_proc.returncode != 0:
        stderr = (push_proc.stderr or "").strip()
        stdout = (push_proc.stdout or "").strip()
        raise RuntimeError(
            f"Git push failed: {stdout or stderr or 'unknown git push error'}"
        )

    head_commit = _run_git(["rev-parse", "HEAD"], repo_root).strip()
    return {
        "repo_root": str(repo_root),
        "remote": remote,
        "branch": branch,
        "commit": head_commit,
        "pushed": True,
        "reason": "success",
        "sic_path": str(sic_path),
    }



def _detect_git_repo_root() -> Optional[Path]:
    proc = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    root = (proc.stdout or "").strip()
    return Path(root) if root else None



def _run_git(args: List[str], cwd: Path) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        stdout = (proc.stdout or "").strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {stdout or stderr or 'unknown git error'}")
    return proc.stdout or ""


# -----------------------------------------------------------------------------
# Orchestration
# -----------------------------------------------------------------------------


def run_dreaming(run_date: Optional[str] = None) -> DreamingRunResult:
    ensure_directories()
    run_date = run_date or datetime.now().strftime("%Y-%m-%d")

    acquire_dreaming_lock()
    try:
        self_page = rehydrate_self_page()
        freeze_day(run_date)
        bundle = collect_daily_signals(run_date)
        sic = perform_self_reflection(bundle)
        sic_path = persist_sic(sic)
        emit_candidate_files(sic)
        append_sic_to_self_page(self_page, sic)
        warm_self_page(self_page)
        seed_next_day_short_term_memory(run_date)
        git_push_result = finalize_with_github_push(run_date, sic_path)
        _write_run_log(run_date, sic_path, git_push_result)
        resolve_missed_cycle(run_date)
        return DreamingRunResult(
            status="completed",
            run_date=run_date,
            sic_path=str(sic_path),
            git_push=git_push_result,
        )
    except Exception as exc:
        _append_runtime_error(
            stage="run_dreaming",
            message=f"Nightly dreaming cycle failed for {run_date}",
            details=traceback.format_exc(),
            severity="critical",
        )
        mark_missed_cycle(run_date, reason=str(exc))
        return DreamingRunResult(
            status="failed",
            run_date=run_date,
            error=str(exc),
            recovery_action="Run recovery cycle on next successful startup.",
        )
    finally:
        release_dreaming_lock()



def _write_run_log(run_date: str, sic_path: Path, git_push: Dict[str, Any]) -> None:
    payload = {
        "run_date": run_date,
        "completed_at": _now_iso(),
        "sic_path": str(sic_path),
        "git_push": git_push,
    }
    write_yaml(LOG_DIR / f"dreaming-{run_date}.yaml", payload)


# -----------------------------------------------------------------------------
# Utility
# -----------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")



def _bundle_hash(bundle: DailySignalBundle) -> str:
    raw = json.dumps(asdict(bundle), sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def run_recovery_cycle() -> List[DreamingRunResult]:
    results: List[DreamingRunResult] = []
    for run_date in recover_missed_cycles():
        results.append(run_dreaming(run_date=run_date))
    return results


if __name__ == "__main__":
    recovery_dates = recover_missed_cycles()
    if recovery_dates:
        print(f"Recovering missed dreaming cycles: {', '.join(recovery_dates)}")
        for result in run_recovery_cycle():
            if result.status == "completed":
                print(f"Recovered {result.run_date}: {result.sic_path}")
            else:
                print(f"Recovery failed for {result.run_date}: {result.error}")

    result = run_dreaming()
    if result.status == "completed":
        pushed = result.git_push.get("pushed") if result.git_push else False
        commit = result.git_push.get("commit") if result.git_push else None
        print(
            f"Nightly dreaming cycle completed. SIC written to: {result.sic_path}. "
            f"GitHub push: {'ok' if pushed else 'skipped'}"
            f"{f' (commit {commit})' if commit else ''}"
        )
    else:
        print(
            f"Nightly dreaming cycle failed for {result.run_date}. "
            f"Recovery action: {result.recovery_action}"
        )
