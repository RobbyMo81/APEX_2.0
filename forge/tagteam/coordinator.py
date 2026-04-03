# Co-authored by FORGE (Session: forge-20260402130446-4010642)
"""TagTeam Coordinator — deterministic code module; no AI calls.

Responsibilities:
  - Owns all prd.json writes via a single fcntl file-lock (TagTeam mode only).
  - Manages story claim leases with heartbeat and expiry.
  - Runs a ghost sweep loop that detects and requeues abandoned claims.
  - Validates tagteam.plan.json via the DAG module before any worktree is created.

Safety gates (non-negotiable):
  - DAG validation must complete before any worktree is created.
  - Coordinator must never spawn agent processes.
  - Every claim release must be logged to audit_log with CLAIM_EXPIRED
    before the claim row is deleted.
"""
from __future__ import annotations

import fcntl
import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .dag import TopologicalOrder, validate_dag


# ── Time helpers ─────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_ts() -> float:
    return time.time()


def _iso_to_ts(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp()


# ── Coordinator ───────────────────────────────────────────────────────────────


class CoordinatorError(RuntimeError):
    pass


class Coordinator:
    """Single-writer Coordinator for TagTeam parallel execution.

    Thread safety: a threading.Lock serialises DB writes from concurrent
    agent completion signals within the same process.  The fcntl file lock
    serialises concurrent *processes* on prd.json writes.

    Args:
        db_path:        Path to forge-memory.db (shared across all worktrees).
        prd_file:       Path to prd.json (only written by this class in TagTeam mode).
        session_id:     Current Forge session ID (used in audit rows).
        agent_timeout:  Expected max runtime of a single agent iteration (seconds).
                        lease_expires_at = claimed_at + agent_timeout + 120s grace.
        ghost_threshold: How old heartbeat_at must be (seconds) before a claim is
                         considered a ghost.  Defaults to agent_timeout.
    """

    _GRACE_SECONDS = 120

    def __init__(
        self,
        db_path: Path,
        prd_file: Path,
        session_id: str,
        agent_timeout: float = 600.0,
        ghost_threshold: float | None = None,
    ) -> None:
        self.db_path = db_path
        self.prd_file = prd_file
        self.session_id = session_id
        self.agent_timeout = agent_timeout
        self.ghost_threshold = (
            ghost_threshold if ghost_threshold is not None else agent_timeout
        )
        self._lock = threading.Lock()

    # ── Low-level DB helpers ─────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def _execute(
        self, sql: str, params: tuple[Any, ...] = (), *, fetch: bool = True
    ) -> list[sqlite3.Row]:
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute(sql, params)
                rows = cur.fetchall() if fetch else []
                conn.commit()
                return rows

    def _audit(
        self,
        conn: sqlite3.Connection,
        action: str,
        story_id: str = "",
        detail: str = "",
    ) -> None:
        conn.execute(
            """
            INSERT INTO audit_log (session_id, story_id, action, detail, ts)
            VALUES (?, ?, ?, ?, ?)
            """,
            (self.session_id, story_id or None, action, detail, _now_iso()),
        )

    # ── Story claim lease management ─────────────────────────────────────────

    def claim_story(self, story_id: str) -> bool:
        """Attempt to claim a story for this session.

        Returns True on success, False if already claimed by another session.
        lease_expires_at = now + agent_timeout + 120s grace.
        """
        now = _now_iso()
        expires_ts = _now_ts() + self.agent_timeout + self._GRACE_SECONDS
        expires_iso = datetime.fromtimestamp(expires_ts, tz=timezone.utc).isoformat()

        with self._lock:
            with self._connect() as conn:
                try:
                    conn.execute(
                        """
                        INSERT INTO story_claims
                            (story_id, session_id, claimed_at, lease_expires_at, heartbeat_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (story_id, self.session_id, now, expires_iso, now),
                    )
                    conn.commit()
                    return True
                except sqlite3.IntegrityError:
                    # Already claimed
                    return False

    def release_claim(self, story_id: str, *, reason: str = "CLAIM_EXPIRED") -> None:
        """Release a claim, writing reason to audit_log before deletion.

        Safety gate: audit write happens within the same transaction as the
        DELETE, so it is impossible for the claim to be deleted without a log.
        """
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM story_claims WHERE story_id = ?", (story_id,)
                ).fetchone()
                if row is None:
                    return  # Already released — idempotent

                elapsed = _now_ts() - _iso_to_ts(row["claimed_at"])
                detail = json.dumps(
                    {
                        "story_id": story_id,
                        "session_id": row["session_id"],
                        "elapsed_seconds": round(elapsed, 1),
                        "heartbeat_at": row["heartbeat_at"],
                        "reason": reason,
                    }
                )
                # Audit BEFORE delete — within same transaction
                self._audit(conn, reason, story_id=story_id, detail=detail)
                conn.execute(
                    "DELETE FROM story_claims WHERE story_id = ?", (story_id,)
                )
                conn.commit()

    def heartbeat(self, story_id: str) -> None:
        """Update heartbeat_at for an active claim (called by the agent loop)."""
        self._execute(
            "UPDATE story_claims SET heartbeat_at = ? WHERE story_id = ? AND session_id = ?",
            (_now_iso(), story_id, self.session_id),
            fetch=False,
        )

    # ── prd.json serialized writer ───────────────────────────────────────────

    def mark_complete(self, story_id: str) -> None:
        """Mark a story as done in prd.json.

        Acquires an exclusive fcntl lock on prd.json before reading and
        writing so concurrent completion signals from multiple agent processes
        are serialised.  Also releases the claim row.
        """
        with self._lock:
            lock_path = self.prd_file.with_suffix(".lock")
            lock_path.touch(exist_ok=True)
            with open(lock_path, "r") as lf:
                fcntl.flock(lf, fcntl.LOCK_EX)
                try:
                    with open(self.prd_file) as fh:
                        prd: dict[str, Any] = json.load(fh)

                    mutated = False
                    for story in prd.get("userStories", []):
                        if story["id"] == story_id:
                            story["status"] = "done"
                            story["passes"] = True
                            mutated = True
                            break

                    if not mutated:
                        raise CoordinatorError(
                            f"mark_complete: story {story_id!r} not found in prd.json"
                        )

                    tmp = self.prd_file.with_suffix(".tmp")
                    tmp.write_text(json.dumps(prd, indent=2) + "\n")
                    tmp.replace(self.prd_file)
                finally:
                    fcntl.flock(lf, fcntl.LOCK_UN)

        # Release the claim row after prd.json is updated
        self.release_claim(story_id, reason="CLAIM_COMPLETE")

    # ── Ghost sweep ──────────────────────────────────────────────────────────

    def ghost_sweep(self) -> list[str]:
        """Detect and requeue ghost claims.

        A claim is a ghost when BOTH conditions hold:
          1. heartbeat_at < now − ghost_threshold
          2. lease_expires_at < now

        Ghost sweep only requeues — never auto-fails — preserving the story
        retry budget.  Each released claim is logged to audit_log with
        action=CLAIM_EXPIRED before the row is deleted.

        Returns:
            List of story_ids that were swept.
        """
        now_ts = _now_ts()
        threshold_ts = now_ts - self.ghost_threshold

        threshold_iso = datetime.fromtimestamp(threshold_ts, tz=timezone.utc).isoformat()
        now_iso = datetime.fromtimestamp(now_ts, tz=timezone.utc).isoformat()

        rows = self._execute(
            """
            SELECT story_id, session_id, heartbeat_at, lease_expires_at, claimed_at
            FROM story_claims
            WHERE heartbeat_at < ? AND lease_expires_at < ?
            """,
            (threshold_iso, now_iso),
        )

        swept: list[str] = []
        for row in rows:
            story_id = row["story_id"]
            # release_claim logs CLAIM_EXPIRED before deleting the row
            self.release_claim(story_id, reason="CLAIM_EXPIRED")
            swept.append(story_id)

        return swept

    def run_ghost_sweep_loop(
        self,
        tick_seconds: float = 60.0,
        stop_event: threading.Event | None = None,
    ) -> None:
        """Run ghost_sweep on a recurring tick (blocking; run in a daemon thread).

        Args:
            tick_seconds:  Interval between sweeps (default 60 s).
            stop_event:    Set this event to stop the loop cleanly.
        """
        if stop_event is None:
            stop_event = threading.Event()

        while not stop_event.is_set():
            swept = self.ghost_sweep()
            if swept:
                print(
                    f"[COORDINATOR] Ghost sweep: requeued {len(swept)} story claim(s): "
                    + ", ".join(swept),
                    flush=True,
                )
            stop_event.wait(timeout=tick_seconds)

    # ── Plan validation ──────────────────────────────────────────────────────

    def validate_plan(
        self,
        plan_file: Path,
        prd_file: Path | None = None,
    ) -> TopologicalOrder:
        """Validate tagteam.plan.json using Kahn's algorithm.

        Hard failures:
          - Circular dependencies → result.has_cycle is True
          - dependsOn IDs not in prd.json → result.phantom_ids is non-empty

        Soft checks (low confidence items):
          - Caller inspects result.order and story confidence fields.

        Args:
            plan_file:  Path to tagteam.plan.json.
            prd_file:   Path to prd.json (defaults to self.prd_file).

        Returns:
            TopologicalOrder; caller must check has_cycle and phantom_ids.
        """
        if prd_file is None:
            prd_file = self.prd_file

        with open(plan_file) as fh:
            plan: dict[str, Any] = json.load(fh)

        with open(prd_file) as fh:
            prd: dict[str, Any] = json.load(fh)

        prd_ids: set[str] = {s["id"] for s in prd.get("userStories", [])}
        plan_stories: list[dict[str, Any]] = plan.get("stories", [])

        # Also validate that all plan storyIds exist in prd.json
        plan_phantom = [
            s["storyId"]
            for s in plan_stories
            if s.get("storyId") not in prd_ids
        ]
        if plan_phantom:
            return TopologicalOrder(
                has_cycle=False,
                phantom_ids=plan_phantom,
                error=(
                    "Plan contains storyIds not in prd.json: "
                    + ", ".join(plan_phantom)
                ),
            )

        return validate_dag(plan_stories, prd_ids)

    def low_confidence_items(self, plan_file: Path) -> list[dict[str, Any]]:
        """Return plan stories where confidence == 'low' for human review."""
        with open(plan_file) as fh:
            plan: dict[str, Any] = json.load(fh)
        return [
            s for s in plan.get("stories", []) if s.get("confidence") == "low"
        ]
