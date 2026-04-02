# Co-authored by FORGE (Session: forge-20260328235846-3946349)
"""
forge/memory.py — Python-owned Forge session and memory lifecycle.

Mirrors the Bash memory layer (forge-memory.sh) for use by the Python
orchestration path.  All methods are idempotent and safe to call
across multiple run_once() iterations within the same session.

Source of truth: forge-memory.sh and forge.sh init_memory() / mark_story_passing()
/ mark_story_failed() / cleanup() (lines 313-332, 756-818).
"""
from __future__ import annotations

import atexit
import os
import signal
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1"

_SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS forge_sessions (
  id            TEXT PRIMARY KEY,
  started_at    TEXT NOT NULL,
  branch_name   TEXT NOT NULL,
  project_name  TEXT NOT NULL,
  max_iterations INTEGER NOT NULL,
  status        TEXT NOT NULL DEFAULT 'running',
  completed_at  TEXT,
  CHECK(status IN ('running','complete','failed','paused'))
);

CREATE TABLE IF NOT EXISTS agent_iterations (
  id            TEXT PRIMARY KEY,
  session_id    TEXT NOT NULL REFERENCES forge_sessions(id),
  iteration     INTEGER NOT NULL,
  story_id      TEXT NOT NULL,
  story_title   TEXT NOT NULL,
  status        TEXT NOT NULL DEFAULT 'running',
  gate_result   TEXT,
  started_at    TEXT NOT NULL,
  completed_at  TEXT,
  CHECK(status IN ('running','pass','fail','blocked')),
  CHECK(gate_result IS NULL OR gate_result IN ('pass','fail','skipped'))
);

CREATE TABLE IF NOT EXISTS agent_messages (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  from_session  TEXT NOT NULL,
  from_iter     INTEGER,
  story_id      TEXT,
  message_type  TEXT NOT NULL,
  subject       TEXT NOT NULL,
  body          TEXT NOT NULL,
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  read_at       TEXT,
  CHECK(message_type IN ('DISCOVERY','BLOCKER','HANDOFF','WARNING','STATUS','DECISION'))
);

CREATE TABLE IF NOT EXISTS context_store (
  key           TEXT NOT NULL,
  scope         TEXT NOT NULL DEFAULT 'global',
  value         TEXT NOT NULL,
  value_type    TEXT NOT NULL DEFAULT 'text',
  written_by    TEXT NOT NULL,
  updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (key, scope)
);

CREATE TABLE IF NOT EXISTS discoveries (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  story_id      TEXT NOT NULL,
  session_id    TEXT NOT NULL,
  iteration     INTEGER NOT NULL,
  type          TEXT NOT NULL,
  title         TEXT NOT NULL,
  detail        TEXT NOT NULL,
  trigger_id    TEXT,
  payload_hash  TEXT,
  source        TEXT,
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  exported_to_agents_md INTEGER NOT NULL DEFAULT 0,
  CHECK(type IN ('PATTERN','GOTCHA','BLOCKER','DECISION','DEPENDENCY','CONVENTION','TRIGGER'))
);

CREATE TABLE IF NOT EXISTS story_state (
  story_id      TEXT PRIMARY KEY,
  attempt_count INTEGER NOT NULL DEFAULT 0,
  last_error    TEXT,
  blockers      TEXT,
  context_notes TEXT,
  last_session  TEXT,
  last_updated  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS audit_log (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id    TEXT NOT NULL,
  iteration     INTEGER,
  story_id      TEXT,
  action        TEXT NOT NULL,
  entity        TEXT,
  detail        TEXT,
  ts            TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS db_meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
INSERT OR IGNORE INTO db_meta VALUES ('schema_version', '1');
INSERT OR IGNORE INTO db_meta VALUES ('created_at', datetime('now'));
INSERT OR IGNORE INTO db_meta VALUES ('project', 'FORGE');
"""


class ForgeMemoryError(RuntimeError):
    pass


class ForgeMemory:
    """Python implementation of the Forge SQLite memory lifecycle.

    Thread safety: use one instance per process.  SQLite WAL mode handles
    concurrent readers from multiple processes.
    """

    _MAX_RETRIES = 5
    _RETRY_BASE_SECONDS = 0.1

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    # ── Low-level query helpers ──────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> list[Any]:
        """Execute *sql* with exponential backoff on SQLITE_BUSY."""
        delay = self._RETRY_BASE_SECONDS
        for attempt in range(1, self._MAX_RETRIES + 1):
            try:
                with self._connect() as conn:
                    cur = conn.execute(sql, params)
                    rows = cur.fetchall()
                    conn.commit()
                    return [tuple(r) for r in rows]
            except sqlite3.OperationalError as exc:
                if "database is locked" in str(exc) and attempt < self._MAX_RETRIES:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise ForgeMemoryError(f"DB error after {attempt} attempt(s): {exc}") from exc
        raise ForgeMemoryError(f"DB locked after {self._MAX_RETRIES} attempts: {sql[:80]}")

    def _scalar(self, sql: str, params: tuple[Any, ...] = ()) -> Any:
        rows = self.execute(sql, params)
        return rows[0][0] if rows else None

    def _fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        return self.execute(sql, params)

    def execute_script(self, sql: str) -> None:
        """Execute a multi-statement SQL script (no parameterisation)."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        delay = self._RETRY_BASE_SECONDS
        for attempt in range(1, self._MAX_RETRIES + 1):
            try:
                conn = sqlite3.connect(str(self.db_path), timeout=30)
                try:
                    conn.executescript(sql)
                    conn.commit()
                finally:
                    conn.close()
                return
            except sqlite3.OperationalError as exc:
                if "database is locked" in str(exc) and attempt < self._MAX_RETRIES:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise ForgeMemoryError(f"DB script error: {exc}") from exc
        raise ForgeMemoryError("DB locked during script execution")

    # ── Schema bootstrap ─────────────────────────────────────────────────

    def init(self) -> None:
        """Create schema if new — mirrors memory_init()."""
        self.execute_script(_SCHEMA_SQL)

    # ── Health check ─────────────────────────────────────────────────────

    def health_check(self) -> None:
        """Verify schema version — raises ForgeMemoryError if wrong.

        Mirrors memory_health_check().
        """
        version = self._scalar("SELECT value FROM db_meta WHERE key='schema_version';")
        if version != SCHEMA_VERSION:
            raise ForgeMemoryError(
                f"Memory DB schema mismatch. Expected v{SCHEMA_VERSION}, found '{version}'. "
                f"Run: rm {self.db_path} and restart."
            )

    # ── Session management ───────────────────────────────────────────────

    def session_exists(self, session_id: str) -> bool:
        count = self._scalar(
            "SELECT COUNT(*) FROM forge_sessions WHERE id=?;", (session_id,)
        )
        return bool(count)

    def create_session(
        self,
        session_id: str,
        branch_name: str,
        project_name: str,
        max_iterations: int,
    ) -> None:
        """Insert a new session row — mirrors memory_create_session().

        Idempotent: silently skips if session_id already exists.
        """
        self.execute(
            "INSERT OR IGNORE INTO forge_sessions"
            "(id, started_at, branch_name, project_name, max_iterations, status)"
            " VALUES(?, datetime('now'), ?, ?, ?, 'running');",
            (session_id, branch_name, project_name, max_iterations),
        )
        self.audit(session_id, None, None, "SESSION_START", "forge_sessions",
                   f"branch={branch_name} project={project_name}")

    def close_session(self, session_id: str, status: str) -> None:
        """Update session to a terminal state — mirrors memory_close_session().

        Valid statuses: complete | failed | paused.
        """
        if status not in {"complete", "failed", "paused"}:
            raise ForgeMemoryError(f"Invalid session close status: '{status}'")
        self.execute(
            "UPDATE forge_sessions SET status=?, completed_at=datetime('now') WHERE id=?;",
            (status, session_id),
        )
        self.audit(session_id, None, None, "SESSION_END", "forge_sessions", f"status={status}")

    # ── Iteration tracking ───────────────────────────────────────────────

    def start_iteration(
        self,
        session_id: str,
        iteration: int,
        story_id: str,
        story_title: str,
    ) -> None:
        """Record a new agent iteration — mirrors memory_start_iteration()."""
        iter_id = f"{session_id}-{iteration}"
        self.execute(
            "INSERT OR REPLACE INTO agent_iterations"
            "(id, session_id, iteration, story_id, story_title, status, started_at)"
            " VALUES(?, ?, ?, ?, ?, 'running', datetime('now'));",
            (iter_id, session_id, iteration, story_id, story_title),
        )
        self.execute(
            "INSERT INTO story_state(story_id, attempt_count, last_session)"
            " VALUES(?, 1, ?)"
            " ON CONFLICT(story_id) DO UPDATE SET"
            "   attempt_count = attempt_count + 1,"
            "   last_session = excluded.last_session,"
            "   last_updated = datetime('now');",
            (story_id, session_id),
        )
        self.audit(session_id, iteration, story_id, "ITERATION_START", "agent_iterations",
                   f"iter_id={iter_id}")

    def end_iteration(
        self,
        session_id: str,
        iteration: int,
        story_id: str,
        status: str,
        gate_result: str,
    ) -> None:
        """Close an agent iteration — mirrors memory_end_iteration()."""
        iter_id = f"{session_id}-{iteration}"
        self.execute(
            "UPDATE agent_iterations SET status=?, gate_result=?, completed_at=datetime('now')"
            " WHERE id=?;",
            (status, gate_result, iter_id),
        )
        self.audit(session_id, iteration, story_id, "ITERATION_END", "agent_iterations",
                   f"status={status} gate={gate_result}")

    # ── Messaging ────────────────────────────────────────────────────────

    def post_message(
        self,
        session_id: str,
        iteration: int | None,
        story_id: str | None,
        msg_type: str,
        subject: str,
        body: str,
    ) -> None:
        """Post an inter-agent message — mirrors memory_post_message()."""
        self.execute(
            "INSERT INTO agent_messages(from_session, from_iter, story_id, message_type, subject, body)"
            " VALUES(?, ?, ?, ?, ?, ?);",
            (session_id, iteration, story_id, msg_type, subject, body),
        )

    # ── Context store ────────────────────────────────────────────────────

    def set_context(
        self,
        key: str,
        value: str,
        scope: str = "global",
        value_type: str = "text",
        written_by: str = "python",
    ) -> None:
        """Upsert a context store entry — mirrors memory_set_context()."""
        self.execute(
            "INSERT INTO context_store(key, scope, value, value_type, written_by, updated_at)"
            " VALUES(?, ?, ?, ?, ?, datetime('now'))"
            " ON CONFLICT(key, scope) DO UPDATE SET"
            "   value=excluded.value, value_type=excluded.value_type,"
            "   written_by=excluded.written_by, updated_at=datetime('now');",
            (key, scope, value, value_type, written_by),
        )

    # ── Audit log ────────────────────────────────────────────────────────

    def audit(
        self,
        session_id: str,
        iteration: int | None,
        story_id: str | None,
        action: str,
        entity: str | None,
        detail: str | None,
    ) -> None:
        """Append an immutable audit record — mirrors memory_audit()."""
        try:
            self.execute(
                "INSERT INTO audit_log(session_id, iteration, story_id, action, entity, detail)"
                " VALUES(?, ?, ?, ?, ?, ?);",
                (session_id, iteration, story_id, action, entity, detail),
            )
        except ForgeMemoryError:
            pass  # audit failures must never abort the main flow

    # ── Startup report ───────────────────────────────────────────────────

    def export_startup_report(
        self,
        session_id: str,
        project_name: str,
        branch_name: str,
        prd_file: Path,
        report_file: Path,
    ) -> None:
        """Write forge-startup-report.md — mirrors memory_export_startup_report()."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        total_sessions = self._scalar("SELECT COUNT(*) FROM forge_sessions;") or 0
        prior_discoveries = self._scalar("SELECT COUNT(*) FROM discoveries;") or 0
        unread_messages = (
            self._scalar("SELECT COUNT(*) FROM agent_messages WHERE read_at IS NULL;") or 0
        )
        last_story = (
            self._scalar(
                "SELECT story_id || ' — ' || completed_at FROM agent_iterations"
                " WHERE status='pass' ORDER BY completed_at DESC LIMIT 1;"
            )
            or ""
        )

        # Unread messages
        msg_rows = self._fetchall(
            "SELECT message_type, subject, coalesce(story_id,'—'), from_session,"
            " coalesce(CAST(from_iter AS TEXT),'?'), body"
            " FROM agent_messages WHERE read_at IS NULL ORDER BY created_at ASC;"
        )
        if msg_rows:
            msg_lines: list[str] = []
            for mtype, subj, sid, fsession, fiter, body in msg_rows:
                msg_lines.append(
                    f"### [{mtype}] {subj}\n"
                    f"**Story:** {sid}\n"
                    f"**From:** Session {fsession} Iteration {fiter}\n"
                    f"{body}\n"
                )
            messages_section = "\n".join(msg_lines)
        else:
            messages_section = "_No unread messages._"

        # Recent discoveries
        disc_rows = self._fetchall(
            "SELECT type, title, detail, story_id, created_at FROM discoveries"
            " ORDER BY created_at DESC LIMIT 10;"
        )
        if disc_rows:
            disc_lines: list[str] = []
            for dtype, title, detail, sid, created_at in disc_rows:
                disc_lines.append(
                    f"### [{dtype}] {title}\n"
                    f"{detail}\n"
                    f"**Story:** {sid} | **Recorded:** {created_at}\n"
                )
            discoveries_section = "\n".join(disc_lines)
        else:
            discoveries_section = "_No discoveries recorded yet._"

        # Context store
        ctx_rows = self._fetchall(
            "SELECT key, scope, value_type, value FROM context_store ORDER BY updated_at DESC;"
        )
        if ctx_rows:
            ctx_lines: list[str] = []
            for key, scope, vtype, value in ctx_rows:
                ctx_lines.append(f"**{key}** ({scope}, {vtype})  \n> {value}\n")
            context_section = "\n".join(ctx_lines)
        else:
            context_section = "_Context store is empty._"

        # Story attempt history
        state_rows = self._fetchall(
            "SELECT story_id, attempt_count, last_updated, context_notes FROM story_state"
            " ORDER BY last_updated DESC;"
        )
        if state_rows:
            state_lines: list[str] = []
            for sid, attempts, updated, notes in state_rows:
                notes_suffix = f". Notes: {notes}" if notes else ""
                state_lines.append(
                    f"- **{sid}** — {attempts} attempt(s). Last: {updated or '—'}{notes_suffix}"
                )
            story_state_section = "\n".join(state_lines)
        else:
            story_state_section = "_No story history yet._"

        report = (
            f"# FORGE Memory System — Startup Report\n"
            f"**Generated:** {now}\n"
            f"**Session ID:** {session_id}\n"
            f"**Project:** {project_name}\n"
            f"**Branch:** {branch_name}\n"
            f"**Memory DB:** {self.db_path}\n"
            f"\n---\n"
            f"\n## Agent Memory State\n"
            f"\n| Metric | Value |\n"
            f"|--------|-------|\n"
            f"| Prior FORGE sessions | {total_sessions} |\n"
            f"| Discoveries in DB | {prior_discoveries} |\n"
            f"| Unread agent messages | {unread_messages} |\n"
            f"| Last story completed | {last_story} |\n"
            f"\n---\n"
            f"\n## Unread Agent Messages\n"
            f"\n{messages_section}\n"
            f"\n---\n"
            f"\n## Recent Discoveries\n"
            f"\n{discoveries_section}\n"
            f"\n---\n"
            f"\n## Context Store Snapshot\n"
            f"\n{context_section}\n"
            f"\n---\n"
            f"\n## Story Attempt History\n"
            f"\n{story_state_section}\n"
            f"\n---\n"
            f"*This file is auto-generated by FORGE at session start. "
            f"It is your primary context briefing. Read it before writing any code.*\n"
        )

        report_file.parent.mkdir(parents=True, exist_ok=True)
        report_file.write_text(report, encoding="utf-8")


# ── Module-level session init helper ────────────────────────────────────────

def init_memory(
    db_path: Path,
    session_id: str,
    branch_name: str,
    project_name: str,
    max_iterations: int,
    forge_db_str: str,
    prd_file: Path,
    report_file: Path,
) -> ForgeMemory:
    """Full init_memory() sequence — mirrors Bash init_memory().

    Performs: init, health_check, create_session, set_context ×4,
    export_startup_report.  Returns the ForgeMemory instance.

    Sidecar startup handoff is the caller's responsibility (V2-035).
    """
    mem = ForgeMemory(db_path)
    mem.init()
    mem.health_check()

    if not mem.session_exists(session_id):
        mem.create_session(session_id, branch_name, project_name, max_iterations)

    mem.set_context("session_id",   session_id,   "global", "text", "python")
    mem.set_context("branch_name",  branch_name,  "global", "text", "python")
    mem.set_context("project_name", project_name, "global", "text", "python")
    mem.set_context("forge_db",     forge_db_str, "global", "path", "python")

    mem.export_startup_report(session_id, project_name, branch_name, prd_file, report_file)

    return mem


def register_abnormal_exit_handler(
    mem: ForgeMemory,
    session_id: str,
) -> None:
    """Register atexit + signal handlers for ABNORMAL_EXIT — mirrors cleanup().

    On non-zero (abnormal) process exit: closes session as 'failed' and
    emits ABNORMAL_EXIT audit.  atexit runs on normal interpreter shutdown too,
    so we track whether the session was already closed normally.
    """
    _closed: dict[str, bool] = {"done": False}

    def _handle_abnormal(exit_code: int) -> None:
        if _closed["done"]:
            return
        _closed["done"] = True
        try:
            mem.close_session(session_id, "failed")
            mem.audit(session_id, None, None, "ABNORMAL_EXIT", "forge.py",
                      f"exit_code={exit_code}")
        except Exception:
            pass

    def _atexit_handler() -> None:
        import sys
        code = getattr(sys, "last_value", None)
        # Only fire on abnormal termination (exception or non-zero via os._exit)
        # We rely on the caller to call close_session() on normal exits.
        if code is not None:
            _handle_abnormal(1)

    def _signal_handler(signum: int, _frame: Any) -> None:
        _handle_abnormal(signum)
        raise SystemExit(signum)

    atexit.register(_atexit_handler)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _signal_handler)
        except (OSError, ValueError):
            pass  # can't set signals in threads or non-main threads

    # Expose a "mark closed" callback so the caller can suppress the atexit handler
    # after a normal session close.
    return _closed
