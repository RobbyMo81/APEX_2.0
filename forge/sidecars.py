# Co-authored by FORGE (Session: forge-20260328235846-3946349)
"""
forge/sidecars.py — Python-owned sidecar orchestration.

Mirrors the Bash sidecar lifecycle from forge.sh:
  - python_venv_init()  (lines 354-371)
  - sidecars_init()     (lines 373-476)
  - sidecars_reap()     (lines 478-526)

Source of truth: forge.sh lines 337-509 and the .sidecars schema in prd.json.
The sidecar health/timeout contract is frozen by V2-035.
"""
from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ── Sidecar schema (frozen by V2-035) ───────────────────────────────────────

@dataclass(frozen=True, slots=True)
class SidecarSpec:
    """Mirrors the PRD .sidecars[] schema used by Bash."""

    id: str
    command: str
    type: str = "unknown"
    cwd: str = "."
    env: dict[str, str] = field(default_factory=dict)
    mandatory: bool = False
    depends_on: list[str] = field(default_factory=list)
    startup_timeout_sec: int = 30
    heartbeat_interval_sec: int = 15
    use_venv: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SidecarSpec":
        return cls(
            id=data["id"],
            command=data["command"],
            type=data.get("type", "unknown"),
            cwd=data.get("cwd", "."),
            env=data.get("env", {}),
            mandatory=bool(data.get("mandatory", False)),
            depends_on=list(data.get("depends_on") or []),
            startup_timeout_sec=int(data.get("startup_timeout_sec", 30)),
            heartbeat_interval_sec=int(data.get("heartbeat_interval_sec", 15)),
            use_venv=bool(data.get("use_venv", False)),
        )


def load_sidecars(prd_file: Path) -> list[SidecarSpec]:
    """Parse the .sidecars array from prd.json.  Returns [] if none."""
    if not prd_file.is_file():
        return []
    data = json.loads(prd_file.read_text(encoding="utf-8"))
    return [SidecarSpec.from_dict(s) for s in data.get("sidecars", [])]


# ── Dependency ordering ──────────────────────────────────────────────────────

def _dependency_levels(sidecars: list[SidecarSpec]) -> list[list[SidecarSpec]]:
    """Return sidecars grouped by dependency level (BFS topological sort).

    Level 0: no depends_on.  Level N: all depends_on satisfied by levels < N.
    Mirrors the level-based logic in forge.sh sidecars_init() lines 385-466.
    """
    remaining = list(sidecars)
    started_ids: set[str] = set()
    levels: list[list[SidecarSpec]] = []

    max_levels = 5
    for _ in range(max_levels):
        ready = [
            s for s in remaining
            if set(s.depends_on).issubset(started_ids)
        ]
        if not ready:
            break
        levels.append(ready)
        for s in ready:
            started_ids.add(s.id)
            remaining.remove(s)

    # Any remaining are unresolvable — they will be caught by mandatory check.
    return levels


# ── Heartbeat check (mirrors memory_check_sidecar) ──────────────────────────

def _check_heartbeat(db_path: Path, sidecar_id: str, max_age_seconds: int) -> bool:
    """Return True if the sidecar posted a HEARTBEAT within max_age_seconds.

    Queries audit_log exactly as memory_check_sidecar() does:
      SELECT ts FROM audit_log WHERE action='HEARTBEAT' AND entity=?
      ORDER BY ts DESC LIMIT 1;
    """
    try:
        conn = sqlite3.connect(str(db_path), timeout=10)
        try:
            cur = conn.execute(
                "SELECT (strftime('%s','now') - strftime('%s', ts))"
                " FROM audit_log"
                " WHERE action='HEARTBEAT' AND entity=?"
                " ORDER BY ts DESC LIMIT 1;",
                (sidecar_id,),
            )
            row = cur.fetchone()
        finally:
            conn.close()
    except Exception:
        return False

    if row is None:
        return False
    age = row[0]
    if age is None:
        return False
    return int(age) <= max_age_seconds


# ── Python venv bootstrap ────────────────────────────────────────────────────

class VenvBootstrapError(RuntimeError):
    pass


def python_venv_init(cwd: Path) -> None:
    """Create and populate .venv if requirements.txt is present.

    Mirrors forge.sh python_venv_init() (lines 354-371).
    """
    requirements = cwd / "requirements.txt"
    venv_dir = cwd / ".venv"

    if not requirements.is_file():
        return  # nothing to do

    if venv_dir.is_dir():
        return  # already bootstrapped

    result = subprocess.run(
        ["python3", "-m", "venv", str(venv_dir)],
        cwd=str(cwd),
        capture_output=True,
    )
    if result.returncode != 0:
        raise VenvBootstrapError(
            f"Failed to create Python virtual environment: {result.stderr.decode()}"
        )

    pip = venv_dir / "bin" / "pip"
    pip_cmd = ["timeout", "120", str(pip), "install", "--quiet", "-r", str(requirements)]
    # Fall back gracefully if 'timeout' binary absent
    try:
        result = subprocess.run(pip_cmd, cwd=str(cwd), capture_output=True)
    except FileNotFoundError:
        pip_cmd = [str(pip), "install", "--quiet", "-r", str(requirements)]
        result = subprocess.run(pip_cmd, cwd=str(cwd), capture_output=True)

    if result.returncode != 0:
        raise VenvBootstrapError(
            f"pip install failed or timed out (120s limit): {result.stderr.decode()}"
        )


# ── Sidecar orchestrator ────────────────────────────────────────────────────

class SidecarStartupError(RuntimeError):
    pass


class SidecarOrchestrator:
    """Python-owned sidecar lifecycle.  Mirrors SIDECAR_PIDS[] in forge.sh."""

    def __init__(self, db_path: Path, repo_root: Path) -> None:
        self.db_path = db_path
        self.repo_root = repo_root
        self._pids: dict[str, int] = {}  # id -> pid

    # ── Public API ───────────────────────────────────────────────────────────

    def start_all(self, sidecars: list[SidecarSpec]) -> None:
        """Start sidecars in dependency order and wait for HEARTBEAT readiness.

        Mirrors forge.sh sidecars_init() (lines 373-476).
        Raises SidecarStartupError if a mandatory sidecar misses its deadline.
        """
        if not sidecars:
            return

        python_venv_init(self.repo_root)

        levels = _dependency_levels(sidecars)
        started_ids: set[str] = set()

        for level_sidecars in levels:
            for spec in level_sidecars:
                self._launch(spec)

            for spec in level_sidecars:
                max_age = spec.heartbeat_interval_sec * 2
                deadline = time.monotonic() + spec.startup_timeout_sec
                healthy = False
                while time.monotonic() < deadline:
                    if _check_heartbeat(self.db_path, spec.id, max_age):
                        healthy = True
                        break
                    time.sleep(1)

                if healthy:
                    started_ids.add(spec.id)
                else:
                    msg = (
                        f"Sidecar [{spec.id}] failed to start or missed heartbeat"
                        f" within {spec.startup_timeout_sec}s."
                    )
                    if spec.mandatory:
                        raise SidecarStartupError(msg)
                    # Non-mandatory: log but continue
                    print(f"[forge/sidecars] WARNING: {msg}", flush=True)

        # Verify all mandatory sidecars are running
        missing = [
            s.id for s in sidecars
            if s.mandatory and s.id not in started_ids
        ]
        if missing:
            raise SidecarStartupError(
                f"Mandatory sidecars failed to start: {', '.join(missing)}"
            )

    def reap_all(self) -> None:
        """Gracefully shut down all started sidecars.

        Contract (frozen by V2-035, mirrors sidecars_reap() lines 478-526):
          1. SIGTERM all living processes.
          2. Wait 10 seconds.
          3. SIGKILL any survivors.
          4. Sleep 1s then verify — warn if any remain.
        """
        if not self._pids:
            return

        # SIGTERM
        for sid, pid in list(self._pids.items()):
            if _pid_alive(pid):
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass

        time.sleep(10)

        # SIGKILL survivors
        for sid, pid in list(self._pids.items()):
            if _pid_alive(pid):
                print(
                    f"[forge/sidecars] WARNING: Sidecar [{sid}] (PID {pid})"
                    " still alive. Sending SIGKILL.",
                    flush=True,
                )
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

        time.sleep(1)

        # Process verification
        surviving = [
            f"{sid}(PID={pid})"
            for sid, pid in self._pids.items()
            if _pid_alive(pid)
        ]
        if surviving:
            print(
                f"[forge/sidecars] SAFETY [Process Isolation]: "
                f"Sidecars still alive after SIGKILL: {' '.join(surviving)}",
                flush=True,
            )
        else:
            print("[forge/sidecars] Process verification: all sidecars confirmed dead.", flush=True)

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _launch(self, spec: SidecarSpec) -> None:
        """Fork the sidecar process in background.  Records PID in self._pids."""
        cwd = self.repo_root / spec.cwd if not Path(spec.cwd).is_absolute() else Path(spec.cwd)
        log_dir = self.repo_root / "tmp" / "sidecars"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"{spec.id}.log"

        env = dict(os.environ)
        env.update(spec.env)

        if spec.use_venv:
            venv_python = cwd / ".venv" / "bin" / "python3"
            if venv_python.is_file():
                env["PATH"] = f"{cwd / '.venv' / 'bin'}:{env.get('PATH', '')}"
                env["VIRTUAL_ENV"] = str(cwd / ".venv")

        with log_file.open("w") as log_handle:
            proc = subprocess.Popen(
                spec.command,
                shell=True,
                cwd=str(cwd),
                env=env,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
            )

        self._pids[spec.id] = proc.pid


# ── Utility ──────────────────────────────────────────────────────────────────

def _pid_alive(pid: int) -> bool:
    """Return True if process *pid* is still running."""
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
