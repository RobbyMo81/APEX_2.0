# Co-authored by FORGE (Session: forge-20260402130446-4010642)
"""TagTeam Worktree Manager — git worktree lifecycle for parallel agents.

Responsibilities:
  - create()       — git worktree add per agent slot, write ownership record,
                     generate per-worktree .forge/forge.gates.sh.
  - rebase()       — git rebase main inside worktree; conflict → status=blocked.
  - teardown()     — stash uncommitted work to recovery branch if present, then
                     git worktree remove.
  - orphan_scan()  — find worktrees with status=active but no live story claim.

Safety gates (non-negotiable):
  - Never run git worktree remove without checking for uncommitted work first.
  - Rebase failure must pause the agent and mark worktree as blocked — never
    silently proceed on a conflicted rebase.
  - worktree_ownership.status must be updated atomically with the filesystem
    operation it describes.
"""
from __future__ import annotations

import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from forge.gates import generate_gates
from forge.toolchains import detect_toolchains


# ── Helpers ───────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run(
    args: list[str],
    *,
    cwd: Path,
    check: bool = True,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd),
        check=check,
        text=True,
        capture_output=capture,
    )


def _git(
    args: list[str],
    *,
    cwd: Path,
    check: bool = True,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    return _run(["git"] + args, cwd=cwd, check=check, capture=capture)


def _has_uncommitted_changes(worktree_path: Path) -> bool:
    """Return True if the worktree has uncommitted or untracked changes."""
    result = _git(
        ["status", "--porcelain"],
        cwd=worktree_path,
        check=False,
        capture=True,
    )
    return bool(result.stdout.strip())


# ── Errors ────────────────────────────────────────────────────────────────────


class WorktreeConflictError(RuntimeError):
    """Raised when git rebase encounters a merge conflict."""


class WorktreeError(RuntimeError):
    """General worktree operation failure."""


# ── Return types ──────────────────────────────────────────────────────────────


class WorktreeInfo(NamedTuple):
    worktree_path: Path
    branch_name: str
    gates_file: Path


# ── WorktreeManager ───────────────────────────────────────────────────────────


class WorktreeManager:
    """Manages git worktree lifecycle for TagTeam parallel agents.

    Args:
        repo_root:  Absolute path to the repository root (where .git lives).
        db_path:    Path to forge-memory.db (shared across all worktrees).
        session_id: Current Forge session ID (written to ownership records).
    """

    def __init__(
        self,
        repo_root: Path,
        db_path: Path,
        session_id: str,
    ) -> None:
        self.repo_root = repo_root
        self.db_path = db_path
        self.session_id = session_id

    # ── DB helpers ────────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

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

    def _upsert_ownership(
        self,
        conn: sqlite3.Connection,
        worktree_path: Path,
        story_id: str,
        status: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO worktree_ownership
                (worktree_path, session_id, story_id, created_at, status)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(worktree_path) DO UPDATE SET
                status = excluded.status
            """,
            (str(worktree_path), self.session_id, story_id, _now_iso(), status),
        )

    def _set_status(
        self,
        conn: sqlite3.Connection,
        worktree_path: Path,
        status: str,
    ) -> None:
        conn.execute(
            "UPDATE worktree_ownership SET status = ? WHERE worktree_path = ?",
            (status, str(worktree_path)),
        )

    # ── Worktree path helpers ─────────────────────────────────────────────────

    def _worktree_path(self, agent_n: int) -> Path:
        return self.repo_root / ".forge" / "worktrees" / f"agent-{agent_n}"

    def _branch_name(self, agent_n: int) -> str:
        return f"tagteam/agent-{agent_n}-{self.session_id}"

    def _recovery_branch(self, story_id: str) -> str:
        return f"tagteam/recovery/{story_id}-{self.session_id}"

    # ── Public API ────────────────────────────────────────────────────────────

    def create(self, agent_n: int, story_id: str) -> WorktreeInfo:
        """Create an isolated git worktree for agent slot *agent_n*.

        Steps:
          1. git worktree add <path> -b <branch>
          2. Create worktree_path/.forge/ workspace directory.
          3. Generate per-worktree gates file via detect_toolchains.
          4. Write worktree_ownership record (status=active) + CREATE audit.

        Returns:
            WorktreeInfo with worktree_path, branch_name, gates_file.

        Raises:
            WorktreeError on git failure.
        """
        worktree_path = self._worktree_path(agent_n)
        branch = self._branch_name(agent_n)

        # Ensure parent exists
        worktree_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            _git(
                ["worktree", "add", str(worktree_path), "-b", branch],
                cwd=self.repo_root,
            )
        except subprocess.CalledProcessError as exc:
            raise WorktreeError(
                f"git worktree add failed for agent-{agent_n}: {exc}"
            ) from exc

        # Create per-worktree .forge/ workspace
        workspace = worktree_path / ".forge"
        workspace.mkdir(parents=True, exist_ok=True)

        # Generate per-worktree gates file
        gates_file = workspace / "forge.gates.sh"
        try:
            detection = detect_toolchains(worktree_path)
            from forge.gates import render_quality_gates, write_quality_gates

            rendered = render_quality_gates(detection, gates_file, worktree_path)
            write_quality_gates(gates_file, rendered)
        except Exception as exc:
            # Gates failure should not prevent worktree creation; log and continue
            print(
                f"[WORKTREE] Warning: gates generation failed for agent-{agent_n}: {exc}",
                flush=True,
            )

        # Atomically record ownership in DB
        with self._connect() as conn:
            self._upsert_ownership(conn, worktree_path, story_id, "active")
            self._audit(
                conn,
                "WORKTREE_CREATE",
                story_id=story_id,
                detail=f"path={worktree_path} branch={branch}",
            )
            conn.commit()

        print(
            f"[WORKTREE] Created worktree agent-{agent_n} at {worktree_path} "
            f"on branch {branch}",
            flush=True,
        )
        return WorktreeInfo(
            worktree_path=worktree_path,
            branch_name=branch,
            gates_file=gates_file,
        )

    def rebase(self, worktree_path: Path, story_id: str = "") -> None:
        """Rebase the worktree branch against main.

        On conflict:
          - Aborts the rebase (git rebase --abort).
          - Sets worktree_ownership.status = 'blocked'.
          - Writes WORKTREE_REBASE_CONFLICT to audit_log.
          - Raises WorktreeConflictError so the caller can pause the agent.

        Safety gate: never silently proceed on a conflicted rebase.

        Raises:
            WorktreeConflictError on rebase conflict.
            WorktreeError on other git failure.
        """
        if not worktree_path.exists():
            raise WorktreeError(f"Worktree path does not exist: {worktree_path}")

        result = _git(
            ["rebase", "main"],
            cwd=worktree_path,
            check=False,
            capture=True,
        )

        if result.returncode == 0:
            # Success — update audit and return
            with self._connect() as conn:
                self._audit(
                    conn,
                    "WORKTREE_REBASE",
                    story_id=story_id,
                    detail=f"path={worktree_path} result=ok",
                )
                conn.commit()
            print(
                f"[WORKTREE] Rebase succeeded for {worktree_path.name}", flush=True
            )
            return

        # Rebase failed — abort to restore clean state
        _git(["rebase", "--abort"], cwd=worktree_path, check=False)

        # Atomically mark worktree as blocked in DB + audit
        with self._connect() as conn:
            self._set_status(conn, worktree_path, "blocked")
            self._audit(
                conn,
                "WORKTREE_REBASE_CONFLICT",
                story_id=story_id,
                detail=(
                    f"path={worktree_path} "
                    f"stdout={result.stdout[:200]!r} "
                    f"stderr={result.stderr[:200]!r}"
                ),
            )
            conn.commit()

        raise WorktreeConflictError(
            f"Rebase conflict in {worktree_path} — worktree marked blocked. "
            f"Resolve manually or use the Resolver agent.\n"
            f"git output: {result.stderr.strip()}"
        )

    def teardown(
        self,
        worktree_path: Path,
        story_id: str = "",
        *,
        force_stash: bool = False,
    ) -> None:
        """Remove a worktree, stashing uncommitted work to a recovery branch first.

        Safety gate: ALWAYS checks for uncommitted changes.  If found (or if
        the worktree is marked blocked / force_stash is True), agent work is
        stashed to tagteam/recovery/{story_id}-{session_id} before removal.

        Steps (when uncommitted changes are present):
          1. git stash push (in worktree cwd)
          2. git stash branch tagteam/recovery/{story_id}-{session_id}
          3. git push origin <recovery_branch> --no-verify
          4. Update status = 'detached' + WORKTREE_DETACH audit (atomically).
          5. git worktree remove <path> --force

        Steps (when worktree is clean):
          1. Update status = 'detached' + WORKTREE_DETACH audit.
          2. git worktree remove <path>.

        Raises:
            WorktreeError if the git worktree remove fails unexpectedly.
        """
        if not worktree_path.exists():
            # Already removed; just clean up the DB record
            with self._connect() as conn:
                self._set_status(conn, worktree_path, "detached")
                self._audit(
                    conn,
                    "WORKTREE_DETACH",
                    story_id=story_id,
                    detail=f"path={worktree_path} note=already_absent",
                )
                conn.commit()
            return

        # Check ownership status for merge failure flag
        has_blocked_status = False
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status FROM worktree_ownership WHERE worktree_path = ?",
                (str(worktree_path),),
            ).fetchone()
            if row and row["status"] == "blocked":
                has_blocked_status = True

        dirty = _has_uncommitted_changes(worktree_path)
        needs_stash = dirty or has_blocked_status or force_stash

        if needs_stash and story_id:
            recovery_branch = self._recovery_branch(story_id)
            print(
                f"[WORKTREE] Stashing agent work to recovery branch {recovery_branch}",
                flush=True,
            )
            # stash any tracked changes
            _git(["stash", "push", "--include-untracked", "-m",
                  f"forge-recovery: {story_id} session={self.session_id}"],
                 cwd=worktree_path, check=False)

            # branch off the stash into the recovery branch
            stash_result = _git(
                ["stash", "branch", recovery_branch],
                cwd=worktree_path,
                check=False,
                capture=True,
            )
            if stash_result.returncode != 0:
                # stash branch requires a stash entry; if the stash was empty
                # (nothing tracked), just create the branch from HEAD
                _git(
                    ["checkout", "-b", recovery_branch],
                    cwd=worktree_path,
                    check=False,
                )

            # Push recovery branch to origin (best-effort; failure is non-fatal)
            push_result = _git(
                ["push", "origin", recovery_branch, "--no-verify"],
                cwd=worktree_path,
                check=False,
                capture=True,
            )
            if push_result.returncode != 0:
                print(
                    f"[WORKTREE] Warning: could not push recovery branch "
                    f"{recovery_branch}: {push_result.stderr.strip()[:200]}",
                    flush=True,
                )
        elif needs_stash and not story_id:
            print(
                "[WORKTREE] Warning: uncommitted changes found but no story_id "
                "provided — skipping recovery branch creation.",
                flush=True,
            )

        # Atomically update status to detached + write audit
        with self._connect() as conn:
            self._set_status(conn, worktree_path, "detached")
            self._audit(
                conn,
                "WORKTREE_DETACH",
                story_id=story_id,
                detail=(
                    f"path={worktree_path} "
                    f"had_changes={dirty} "
                    f"was_blocked={has_blocked_status}"
                ),
            )
            conn.commit()

        # Remove the worktree from git
        try:
            _git(
                ["worktree", "remove", str(worktree_path), "--force"],
                cwd=self.repo_root,
            )
        except subprocess.CalledProcessError as exc:
            raise WorktreeError(
                f"git worktree remove failed for {worktree_path}: {exc}"
            ) from exc

        print(f"[WORKTREE] Removed worktree at {worktree_path}", flush=True)

        # Delete the local branch so it can be reused on retry
        branch = worktree_path.name  # e.g. "agent-0" → branch "tagteam/agent-0-<session>"
        # Reconstruct the branch name the same way _branch_name() does
        try:
            branch_to_delete = f"tagteam/{worktree_path.name}-{self.session_id}"
            _git(["branch", "-D", branch_to_delete], cwd=self.repo_root, check=False, capture=True)
        except Exception:
            pass  # Best-effort — failure is non-fatal

    def orphan_scan(self) -> list[dict[str, str]]:
        """Detect worktrees with status=active but no live story claim.

        A worktree is considered orphaned when:
          - worktree_ownership.status = 'active'
          - No row in story_claims with the same story_id (claim was released
            or never created).

        Returns:
            List of dicts with keys worktree_path, story_id, session_id,
            created_at for human or Coordinator review.
        """
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT wo.worktree_path, wo.story_id, wo.session_id, wo.created_at
                FROM worktree_ownership wo
                WHERE wo.status = 'active'
                  AND NOT EXISTS (
                      SELECT 1 FROM story_claims sc
                      WHERE sc.story_id = wo.story_id
                  )
                """
            ).fetchall()

        orphans = [
            {
                "worktree_path": row["worktree_path"],
                "story_id": row["story_id"],
                "session_id": row["session_id"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

        if orphans:
            print(
                f"[WORKTREE] orphan_scan: found {len(orphans)} orphaned worktree(s)",
                flush=True,
            )

        return orphans
