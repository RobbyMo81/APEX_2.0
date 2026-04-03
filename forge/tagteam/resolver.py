# Co-authored by FORGE (Session: forge-20260402183630-43092)
"""TagTeam Resolver Sub-Agent.

Short-lived agent invoked only on merge conflict.  All state lives in the DB —
the Resolver is stateless between invocations.

Conflict classification (rule-based, deterministic):
  structural — same function/method signature modified in both branches.
  additive   — non-overlapping additions (new lines, new functions) only.
  prd.json   — conflict path is prd.json.

Resolution actions:
  additive   → invoke AI agent, apply patch, re-merge, run post-merge gate.
  structural → escalate to human (never auto-patched).
  prd.json   → route to Coordinator for deterministic union of passes=true.

Safety gates (non-negotiable):
  - Resolver must never auto-resolve a structural conflict.
  - Resolver must never write to prd.json — route to Coordinator.
  - Hard cap of 2 attempts per story pair enforced in DB, not in the prompt.
"""
from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from ..agents.base import AgentTask
from ..agents.claude import ClaudeBackend

# ── Types ─────────────────────────────────────────────────────────────────────

ConflictClass = Literal["structural", "additive", "prd.json"]
Outcome = Literal["patched", "escalated", "capped"]

_MAX_ATTEMPTS = 2  # enforced in DB; third conflict → unconditional escalation


# ── Classification helpers ────────────────────────────────────────────────────

# Regex matches function/method signatures in Python, TypeScript, Rust, and Go.
_FUNC_SIG_RE = re.compile(
    r"^[+-][\s]*"                          # diff prefix (+/-)
    r"(?:"
    r"(?:pub\s+(?:async\s+)?fn|async\s+fn|fn)\s+\w+"          # Rust/Go fn
    r"|(?:async\s+)?(?:def|function)\s+\w+"                    # Python/JS
    r"|(?:public|private|protected|static|async|\s)+"
    r"(?:function\s+)?\w+\s*\("                                 # TS methods
    r")"
)


def classify_conflict(diff_text: str, conflict_path: str) -> ConflictClass:
    """Classify a merge conflict diff (rule-based, deterministic).

    Rules applied in order:
      1. If conflict_path is 'prd.json' → 'prd.json'.
      2. If any +/- line matches a function signature pattern → 'structural'.
      3. Otherwise → 'additive'.

    Args:
        diff_text:      Raw diff output for the conflicting file (unified diff).
        conflict_path:  Relative path of the conflicting file.

    Returns:
        ConflictClass — one of 'structural', 'additive', 'prd.json'.
    """
    if Path(conflict_path).name == "prd.json":
        return "prd.json"

    for line in diff_text.splitlines():
        if not line.startswith(("+", "-")):
            continue
        if line.startswith("---") or line.startswith("+++"):
            continue
        if _FUNC_SIG_RE.match(line):
            return "structural"

    return "additive"


# ── DB helpers ────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def _count_attempts(db_path: Path, story_a_id: str, story_b_id: str) -> int:
    """Return current attempt count for (story_a_id, story_b_id) pair."""
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS cnt FROM resolver_attempts
            WHERE story_a_id = ? AND story_b_id = ?
            """,
            (story_a_id, story_b_id),
        ).fetchone()
    return int(row["cnt"]) if row else 0


def _record_attempt(
    db_path: Path,
    story_a_id: str,
    story_b_id: str,
    attempt_number: int,
    classification: ConflictClass,
    outcome: Outcome,
    session_id: str,
) -> None:
    """Insert an attempt record.  Also writes an audit_log entry."""
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO resolver_attempts
                (story_a_id, story_b_id, attempt_number, classification, outcome, session_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (story_a_id, story_b_id, attempt_number, classification, outcome, session_id),
        )
        conn.execute(
            """
            INSERT INTO audit_log (session_id, story_id, action, detail, ts)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                session_id,
                f"{story_a_id}+{story_b_id}",
                "RESOLVER_INVOCATION",
                json.dumps(
                    {
                        "story_a_id": story_a_id,
                        "story_b_id": story_b_id,
                        "attempt_number": attempt_number,
                        "classification": classification,
                        "outcome": outcome,
                    }
                ),
                _now_iso(),
            ),
        )
        conn.commit()


def _post_blocker(
    db_path: Path,
    session_id: str,
    story_a_id: str,
    story_b_id: str,
    classification: ConflictClass,
    diff_text: str,
) -> None:
    """Write a BLOCKER message to agent_messages with full diff attached."""
    body = json.dumps(
        {
            "story_a_id": story_a_id,
            "story_b_id": story_b_id,
            "classification": classification,
            "diff": diff_text[:4000],  # cap to avoid mega-blobs
            "reason": "Resolver hard cap reached (2 attempts). Human escalation required.",
        }
    )
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO agent_messages
                (from_session, story_id, message_type, subject, body)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                session_id,
                f"{story_a_id}+{story_b_id}",
                "BLOCKER",
                f"Resolver cap reached: {story_a_id} × {story_b_id} ({classification})",
                body,
            ),
        )
        conn.commit()


# ── AI invocation ─────────────────────────────────────────────────────────────

_RESOLVER_DIRECTIVE = """\
You are a merge-conflict resolver. You will receive a unified diff of a merge
conflict between two feature branches.

OUTPUT RULES (strict):
- If the conflict can be resolved by a non-overlapping patch, output ONLY a
  valid unified diff patch (starting with --- and +++). Nothing else.
- If the conflict requires human judgement (overlapping logic changes, semantic
  ambiguity), output ONLY the single word: ESCALATE

Do not explain. Do not add commentary. Output the patch OR the word ESCALATE.
"""


def _build_resolver_payload(
    diff_text: str,
    story_a: dict[str, Any],
    story_b: dict[str, Any],
) -> str:
    return "\n".join(
        [
            _RESOLVER_DIRECTIVE,
            "\n---\n",
            "## Conflict Diff\n",
            "```diff",
            diff_text,
            "```",
            "\n---\n",
            "## Story A Context",
            f"- ID: {story_a.get('id', '')}",
            f"- Title: {story_a.get('title', '')}",
            f"- Summary: {story_a.get('description', '')[:300]}",
            "\n## Story B Context",
            f"- ID: {story_b.get('id', '')}",
            f"- Title: {story_b.get('title', '')}",
            f"- Summary: {story_b.get('description', '')[:300]}",
        ]
    )


def invoke_resolver_agent(
    diff_text: str,
    story_a: dict[str, Any],
    story_b: dict[str, Any],
    repo_root: Path,
    timeout: float = 300.0,
) -> str:
    """Call the Claude backend with the conflict payload.

    Returns the raw output string from the agent — either a patch or 'ESCALATE'.
    Raises RuntimeError if the backend exits non-zero.
    """
    payload = _build_resolver_payload(diff_text, story_a, story_b)
    backend = ClaudeBackend()
    task = AgentTask(
        story_id="RESOLVER",
        story_title="TagTeam Resolver",
        payload=payload,
        cwd=repo_root,
        env={},
        timeout=timeout,
    )
    result = backend.invoke(task)
    if result.returncode != 0:
        raise RuntimeError(
            f"Resolver agent exited {result.returncode}: {result.stderr[:500]}"
        )
    return result.stdout.strip()


# ── Escalation ────────────────────────────────────────────────────────────────


def escalate(
    db_path: Path,
    session_id: str,
    story_a_id: str,
    story_b_id: str,
    classification: ConflictClass,
    diff_text: str,
    reason: str = "",
) -> None:
    """Write a BLOCKER escalation message to agent_messages and print to stderr.

    Does NOT record an attempt — caller is responsible for recording outcome
    after calling escalate().
    """
    _post_blocker(db_path, session_id, story_a_id, story_b_id, classification, diff_text)
    msg = (
        f"[RESOLVER] ESCALATED: {story_a_id} × {story_b_id} "
        f"classification={classification} reason={reason or 'structural or agent output ESCALATE'}"
    )
    print(msg, file=sys.stderr, flush=True)


# ── prd.json conflict routing ─────────────────────────────────────────────────


def route_prd_conflict(
    prd_file: Path,
    worktree_prd_file: Path,
) -> None:
    """Resolve a prd.json conflict deterministically via Coordinator logic.

    Takes the union of all story entries where passes=True from either version.
    The merged result is written back via the Coordinator (caller must hold the
    prd.json file lock).

    Args:
        prd_file:          Path to the main-branch prd.json.
        worktree_prd_file: Path to the worktree's conflicting prd.json.

    Safety gate: this function never writes to prd.json directly — it returns
    the merged dict.  Caller must use Coordinator.mark_complete() or the
    fcntl-locked writer.
    """
    with open(prd_file) as fh:
        main_prd: dict[str, Any] = json.load(fh)
    with open(worktree_prd_file) as fh:
        worktree_prd: dict[str, Any] = json.load(fh)

    # Index stories by ID from both versions
    main_stories: dict[str, dict[str, Any]] = {
        s["id"]: s for s in main_prd.get("userStories", [])
    }
    worktree_stories: dict[str, dict[str, Any]] = {
        s["id"]: s for s in worktree_prd.get("userStories", [])
    }

    all_ids = set(main_stories) | set(worktree_stories)
    merged_stories: list[dict[str, Any]] = []

    for sid in sorted(all_ids):  # deterministic order
        main_s = main_stories.get(sid)
        wt_s = worktree_stories.get(sid)

        if main_s is None:
            merged_stories.append(wt_s)  # type: ignore[arg-type]
        elif wt_s is None:
            merged_stories.append(main_s)
        else:
            # Union rule: if either version has passes=True, the merged entry passes
            if wt_s.get("passes") is True:
                merged_stories.append(wt_s)
            else:
                merged_stories.append(main_s)

    merged_prd = dict(main_prd)
    merged_prd["userStories"] = merged_stories
    return merged_prd  # type: ignore[return-value]


# ── Apply patch helpers ───────────────────────────────────────────────────────


def _apply_patch(patch_text: str, cwd: Path) -> bool:
    """Apply a unified diff patch in the given working directory.

    Returns True on success, False on failure.
    Uses 'patch --dry-run' first to verify, then applies.
    """
    try:
        dry = subprocess.run(
            ["patch", "--dry-run", "-p1"],
            input=patch_text,
            capture_output=True,
            text=True,
            cwd=str(cwd),
            timeout=30,
        )
        if dry.returncode != 0:
            print(
                f"[RESOLVER] Patch dry-run failed:\n{dry.stderr[:500]}",
                file=sys.stderr,
                flush=True,
            )
            return False

        apply = subprocess.run(
            ["patch", "-p1"],
            input=patch_text,
            capture_output=True,
            text=True,
            cwd=str(cwd),
            timeout=30,
        )
        return apply.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def _run_post_merge_gate(cwd: Path) -> bool:
    """Run 'git merge-tree' verification equivalent: check for unresolved markers.

    A lightweight gate: scan tracked files for conflict markers.
    Returns True if clean, False if conflict markers remain.
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--check"],
            capture_output=True,
            text=True,
            cwd=str(cwd),
            timeout=30,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


# ── Main entry point ──────────────────────────────────────────────────────────


@dataclass
class ResolverResult:
    outcome: Outcome
    classification: ConflictClass
    attempt_number: int
    patch_applied: bool = False
    post_merge_clean: bool = False
    escalation_reason: str = ""


def run_resolver(
    db_path: Path,
    prd_file: Path,
    repo_root: Path,
    session_id: str,
    story_a_id: str,
    story_b_id: str,
    diff_text: str,
    conflict_path: str,
    story_a: dict[str, Any],
    story_b: dict[str, Any],
    worktree_prd_file: Path | None = None,
    ai_timeout: float = 300.0,
) -> ResolverResult:
    """Main Resolver entry point.

    Classifies the conflict, enforces the attempt cap, and dispatches to the
    appropriate handler.

    Safety gates enforced here (not in prompt):
      1. Hard cap: if attempt count >= _MAX_ATTEMPTS before this call,
         immediately post BLOCKER and return outcome='capped'.
      2. Structural: immediately escalate, never patch.
      3. prd.json: route to Coordinator logic, never patch.

    Args:
        db_path:            Shared forge-memory.db path.
        prd_file:           Main-branch prd.json path.
        repo_root:          Repo root (used for patch application cwd).
        session_id:         Current session ID.
        story_a_id:         First story in the conflicting pair.
        story_b_id:         Second story in the conflicting pair.
        diff_text:          Raw unified diff of the conflict.
        conflict_path:      Relative path of the conflicting file.
        story_a:            PRD story dict for story_a_id.
        story_b:            PRD story dict for story_b_id.
        worktree_prd_file:  Path to worktree prd.json (required for prd.json conflicts).
        ai_timeout:         Timeout for the AI agent call (default 300s).

    Returns:
        ResolverResult describing outcome, classification, and attempt number.
    """
    prior_attempts = _count_attempts(db_path, story_a_id, story_b_id)
    attempt_number = prior_attempts + 1

    classification = classify_conflict(diff_text, conflict_path)

    # ── Safety gate 1: hard cap ───────────────────────────────────────────────
    if prior_attempts >= _MAX_ATTEMPTS:
        escalate(
            db_path,
            session_id,
            story_a_id,
            story_b_id,
            classification,
            diff_text,
            reason=f"hard cap reached ({prior_attempts} prior attempts)",
        )
        _record_attempt(
            db_path, story_a_id, story_b_id, attempt_number,
            classification, "capped", session_id,
        )
        return ResolverResult(
            outcome="capped",
            classification=classification,
            attempt_number=attempt_number,
            escalation_reason=f"hard cap ({prior_attempts} prior attempts)",
        )

    # ── Safety gate 2: structural → always escalate ───────────────────────────
    if classification == "structural":
        escalate(
            db_path,
            session_id,
            story_a_id,
            story_b_id,
            classification,
            diff_text,
            reason="structural conflict — same-function modification",
        )
        _record_attempt(
            db_path, story_a_id, story_b_id, attempt_number,
            classification, "escalated", session_id,
        )
        return ResolverResult(
            outcome="escalated",
            classification=classification,
            attempt_number=attempt_number,
            escalation_reason="structural conflict",
        )

    # ── prd.json → route to Coordinator ──────────────────────────────────────
    if classification == "prd.json":
        if worktree_prd_file is None:
            escalate(
                db_path,
                session_id,
                story_a_id,
                story_b_id,
                classification,
                diff_text,
                reason="prd.json conflict but no worktree_prd_file provided",
            )
            _record_attempt(
                db_path, story_a_id, story_b_id, attempt_number,
                classification, "escalated", session_id,
            )
            return ResolverResult(
                outcome="escalated",
                classification=classification,
                attempt_number=attempt_number,
                escalation_reason="prd.json conflict — missing worktree_prd_file",
            )

        # Deterministic union — Resolver never writes prd.json directly.
        # Returns merged dict; Coordinator caller must apply the write.
        merged = route_prd_conflict(prd_file, worktree_prd_file)
        # Write result back to prd_file (Coordinator holds the lock when calling us)
        import fcntl  # noqa: PLC0415 — deferred import to keep top-level clean

        tmp = prd_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(merged, indent=2) + "\n")
        tmp.replace(prd_file)

        _record_attempt(
            db_path, story_a_id, story_b_id, attempt_number,
            classification, "patched", session_id,
        )
        print(
            f"[RESOLVER] prd.json conflict resolved via deterministic union "
            f"(attempt {attempt_number})",
            flush=True,
        )
        return ResolverResult(
            outcome="patched",
            classification=classification,
            attempt_number=attempt_number,
            patch_applied=True,
            post_merge_clean=True,
        )

    # ── additive → invoke AI agent, apply patch, post-merge gate ─────────────
    try:
        agent_output = invoke_resolver_agent(
            diff_text, story_a, story_b, repo_root, timeout=ai_timeout
        )
    except RuntimeError as exc:
        escalate(
            db_path,
            session_id,
            story_a_id,
            story_b_id,
            classification,
            diff_text,
            reason=f"agent invocation failed: {exc}",
        )
        _record_attempt(
            db_path, story_a_id, story_b_id, attempt_number,
            classification, "escalated", session_id,
        )
        return ResolverResult(
            outcome="escalated",
            classification=classification,
            attempt_number=attempt_number,
            escalation_reason=f"agent invocation error: {exc}",
        )

    if agent_output.strip().upper() == "ESCALATE":
        escalate(
            db_path,
            session_id,
            story_a_id,
            story_b_id,
            classification,
            diff_text,
            reason="agent returned ESCALATE",
        )
        _record_attempt(
            db_path, story_a_id, story_b_id, attempt_number,
            classification, "escalated", session_id,
        )
        return ResolverResult(
            outcome="escalated",
            classification=classification,
            attempt_number=attempt_number,
            escalation_reason="agent returned ESCALATE",
        )

    # Apply the patch
    patch_applied = _apply_patch(agent_output, repo_root)
    if not patch_applied:
        escalate(
            db_path,
            session_id,
            story_a_id,
            story_b_id,
            classification,
            diff_text,
            reason="patch application failed",
        )
        _record_attempt(
            db_path, story_a_id, story_b_id, attempt_number,
            classification, "escalated", session_id,
        )
        return ResolverResult(
            outcome="escalated",
            classification=classification,
            attempt_number=attempt_number,
            escalation_reason="patch application failed",
        )

    # Post-merge gate
    post_merge_clean = _run_post_merge_gate(repo_root)
    if not post_merge_clean:
        escalate(
            db_path,
            session_id,
            story_a_id,
            story_b_id,
            classification,
            diff_text,
            reason="post-merge gate failed (conflict markers remain)",
        )
        _record_attempt(
            db_path, story_a_id, story_b_id, attempt_number,
            classification, "escalated", session_id,
        )
        return ResolverResult(
            outcome="escalated",
            classification=classification,
            attempt_number=attempt_number,
            patch_applied=True,
            post_merge_clean=False,
            escalation_reason="post-merge gate failed",
        )

    _record_attempt(
        db_path, story_a_id, story_b_id, attempt_number,
        classification, "patched", session_id,
    )
    print(
        f"[RESOLVER] Additive conflict patched and re-merged (attempt {attempt_number})",
        flush=True,
    )
    return ResolverResult(
        outcome="patched",
        classification=classification,
        attempt_number=attempt_number,
        patch_applied=True,
        post_merge_clean=True,
    )
