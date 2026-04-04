# Co-authored by FORGE (Session: forge-20260402130446-4010642)
"""TagTeam Orchestrator — deterministic wave scheduler and dispatch.

No AI calls.  All routing is deterministic from plan state and story state.

Responsibilities:
  - Compute the ready queue from tagteam.plan.json dependsOn graph.
  - Dispatch stories to idle agent slots up to max_agents.
  - Assign backends using Planner hints with retry switching (3 retries → blocked).
  - Run a post-wave integration gate on main after each wave completes.
  - Requeue stories on agent failure.

Safety gates (non-negotiable):
  - Orchestrator must never write prd.json — all completion signals route
    through Coordinator.
  - Post-wave integration gate failure MUST block the next wave — not just
    log a warning.
  - Orchestrator must not start if check-plan has unreviewed confidence low
    items.
  - Orchestrator must never spawn Coordinator or WorktreeManager
    sub-processes — it calls their Python APIs only.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..models import ForgeConfig
from ..runner import run_quality_gates
from .coordinator import Coordinator
from .worktree import WorktreeConflictError, WorktreeInfo, WorktreeManager


# ── Errors ────────────────────────────────────────────────────────────────────


class OrchestratorError(RuntimeError):
    """Raised on non-recoverable Orchestrator failures."""


# ── Data structures ───────────────────────────────────────────────────────────


@dataclass
class SlotState:
    """Live state for one active agent slot."""

    slot_idx: int
    story_id: str
    backend: str
    process: subprocess.Popen  # type: ignore[type-arg]
    worktree_info: WorktreeInfo


@dataclass
class OrchestratorResult:
    """Result returned by Orchestrator.run()."""

    status: str  # "complete" | "gate_failed" | "blocked" | "no_plan"
    stories_completed: list[str] = field(default_factory=list)
    stories_blocked: list[str] = field(default_factory=list)
    gate_failure_wave: int = 0


# ── Orchestrator ──────────────────────────────────────────────────────────────

_OTHER_BACKEND: dict[str, str] = {"claude": "codex", "codex": "claude"}


class Orchestrator:
    """Wave-based parallel story dispatcher for TagTeam mode.

    Args:
        config:           Resolved ForgeConfig (for repo_root, workspace_dir,
                          db_path, agent_backend).
        max_agents:       Maximum concurrent agent slots.
        coordinator:      Shared Coordinator instance.
        worktree_manager: Shared WorktreeManager instance.
        plan_file:        Path to tagteam.plan.json.
        prd_file:         Path to prd.json (read-only for the Orchestrator).
        poll_interval:    Seconds between process-poll sweeps (default 2.0).
        ghost_sweep_tick: Seconds between ghost sweeps (default 60.0).
    """

    _MAX_RETRIES = 3  # third retry → blocked

    def __init__(
        self,
        config: ForgeConfig,
        max_agents: int,
        coordinator: Coordinator,
        worktree_manager: WorktreeManager,
        plan_file: Path,
        prd_file: Path,
        poll_interval: float = 2.0,
        ghost_sweep_tick: float = 60.0,
    ) -> None:
        self.config = config
        self.max_agents = max_agents
        self.coordinator = coordinator
        self.worktree_manager = worktree_manager
        self.plan_file = plan_file
        self.prd_file = prd_file
        self.poll_interval = poll_interval
        self.ghost_sweep_tick = ghost_sweep_tick

        # Active slots: slot_idx → SlotState
        self._active_slots: dict[int, SlotState] = {}

        # In-memory retry tracking: story_id → list of backends tried
        self._tried_backends: dict[str, list[str]] = {}

    # ── Ready queue ───────────────────────────────────────────────────────────

    def ready_queue(self) -> list[str]:
        """Return story IDs ready to run.

        A story is ready when:
          - Its passes flag is False (not yet completed).
          - All dependsOn story IDs have passes=True.
          - It is not currently claimed / active in a slot.
          - It has not been marked blocked (>= _MAX_RETRIES failed attempts).
        """
        with open(self.plan_file) as fh:
            plan: dict[str, Any] = json.load(fh)
        with open(self.prd_file) as fh:
            prd: dict[str, Any] = json.load(fh)

        passes: dict[str, bool] = {
            s["id"]: bool(s.get("passes", False))
            for s in prd.get("userStories", [])
        }

        # Stories currently running in active slots
        running: set[str] = {s.story_id for s in self._active_slots.values()}

        ready: list[str] = []
        for story in plan.get("stories", []):
            sid: str = story["storyId"]

            # Already complete
            if passes.get(sid, False):
                continue

            # Already running
            if sid in running:
                continue

            # Backend retry exhausted → permanently blocked for this run
            if len(self._tried_backends.get(sid, [])) >= self._MAX_RETRIES:
                continue

            # All deps must pass
            deps: list[str] = story.get("dependsOn", [])
            if all(passes.get(dep, False) for dep in deps):
                ready.append(sid)

        return ready

    # ── Backend selection ─────────────────────────────────────────────────────

    def _plan_story(self, story_id: str) -> dict[str, Any]:
        with open(self.plan_file) as fh:
            plan: dict[str, Any] = json.load(fh)
        for s in plan.get("stories", []):
            if s["storyId"] == story_id:
                return s
        return {}

    def _pick_backend(self, story_id: str) -> str | None:
        """Return the backend to use for this attempt, or None if exhausted.

        Retry sequence (0-indexed attempt number):
          0 → preferredBackend (or config.agent_backend if no hint)
          1 → other backend
          2 → revert to preferred
          3+ → None (mark story blocked)
        """
        plan_story = self._plan_story(story_id)
        preferred: str = (
            plan_story.get("preferredBackend")  # type: ignore[assignment]
            or self.config.agent_backend
            or "claude"
        )
        # If FORGE_AGENT_BACKEND is explicitly set, honour it on every retry —
        # don't switch to codex which runs in a read-only sandbox.
        env_backend = os.environ.get("FORGE_AGENT_BACKEND", "")
        if env_backend:
            preferred = env_backend
        other = _OTHER_BACKEND.get(preferred, "claude")
        # Never switch to codex — it cannot write files in its default sandbox.
        if other == "codex":
            other = preferred
        tried = self._tried_backends.get(story_id, [])
        attempt = len(tried)
        if attempt == 0:
            return preferred
        if attempt == 1:
            return other
        if attempt == 2:
            return preferred
        return None  # exhausted — caller should mark blocked

    # ── Dispatch ──────────────────────────────────────────────────────────────

    def dispatch(self, story_id: str, slot_idx: int, backend: str) -> SlotState:
        """Claim *story_id*, create a worktree, and start the agent subprocess.

        Returns the populated SlotState for the caller to track.

        Raises:
            OrchestratorError on claim failure or worktree creation failure.
        """
        # 1. Claim story via Coordinator
        if not self.coordinator.claim_story(story_id):
            raise OrchestratorError(
                f"dispatch: story {story_id!r} could not be claimed "
                "(already held by another session)"
            )

        # 2. Create isolated worktree via WorktreeManager
        try:
            worktree_info = self.worktree_manager.create(slot_idx, story_id)
        except Exception as exc:
            # Release the claim we just acquired
            self.coordinator.release_claim(story_id, reason="DISPATCH_WORKTREE_FAIL")
            raise OrchestratorError(
                f"dispatch: worktree creation failed for story {story_id!r}: {exc}"
            ) from exc

        # 3. Build environment for agent subprocess
        # Explicitly forward FORGE_PRD_FILE and FORGE_WORKSPACE_DIR so worktree
        # subprocesses use the correct prd.json and shared workspace, regardless
        # of whether the caller's shell exported these vars.
        env = {
            **os.environ,
            "FORGE_TAGTEAM": "true",
            "FORGE_TAGTEAM_STORY_ID": story_id,
            "FORGE_AGENT_BACKEND": backend,
            "FORGE_PRD_FILE": str(self.prd_file.resolve()),
            "FORGE_WORKSPACE_DIR": str(self.config.workspace_dir),
        }

        # 4. Determine gates file for this worktree
        gates_file = worktree_info.gates_file
        if gates_file.exists():
            env["FORGE_GATES_FILE"] = str(gates_file)

        # 5. Spawn agent subprocess in worktree cwd
        python_cmd = _python_cmd()
        proc = subprocess.Popen(
            [python_cmd, "-m", "forge", "run-once"],
            cwd=str(worktree_info.worktree_path),
            env=env,
        )

        # Track backend attempt (before process exits, so we know what was tried)
        self._tried_backends.setdefault(story_id, []).append(backend)

        return SlotState(
            slot_idx=slot_idx,
            story_id=story_id,
            backend=backend,
            process=proc,
            worktree_info=worktree_info,
        )

    # ── Story lifecycle callbacks ─────────────────────────────────────────────

    def on_story_complete(self, slot: SlotState) -> None:
        """Signal Coordinator, tear down worktree, rebase other active worktrees.

        Safety gate: only Coordinator writes prd.json.
        """
        # 1. Signal Coordinator to update prd.json
        self.coordinator.mark_complete(slot.story_id)

        # 2. Tear down this worktree (story merged cleanly)
        try:
            self.worktree_manager.teardown(
                slot.worktree_info.worktree_path,
                slot.story_id,
            )
        except Exception as exc:
            print(
                f"[ORCHESTRATOR] Warning: worktree teardown failed for "
                f"{slot.story_id!r}: {exc}",
                flush=True,
            )

        # 3. Rebase other active worktrees against main
        for other_slot in list(self._active_slots.values()):
            if other_slot.slot_idx == slot.slot_idx:
                continue
            try:
                self.worktree_manager.rebase(
                    other_slot.worktree_info.worktree_path,
                    other_slot.story_id,
                )
            except WorktreeConflictError:
                print(
                    f"[ORCHESTRATOR] Rebase conflict on slot {other_slot.slot_idx} "
                    f"(story {other_slot.story_id!r}) — worktree marked blocked.",
                    flush=True,
                )
            except Exception as exc:
                print(
                    f"[ORCHESTRATOR] Warning: rebase failed for story "
                    f"{other_slot.story_id!r}: {exc}",
                    flush=True,
                )

        # 4. Remove slot from active tracking
        self._active_slots.pop(slot.slot_idx, None)

    def on_story_fail(self, slot: SlotState) -> None:
        """Release claim, tear down worktree.  Story stays unpassed for retry."""
        self.coordinator.release_claim(slot.story_id, reason="STORY_FAIL")
        try:
            self.worktree_manager.teardown(
                slot.worktree_info.worktree_path,
                slot.story_id,
            )
        except Exception as exc:
            print(
                f"[ORCHESTRATOR] Warning: worktree teardown on failure for "
                f"{slot.story_id!r}: {exc}",
                flush=True,
            )
        self._active_slots.pop(slot.slot_idx, None)

    # ── Post-wave integration gate ────────────────────────────────────────────

    def _run_post_wave_gate(self) -> bool:
        """Run quality gates against the main repo root.

        Returns True on pass, False on failure.
        Post-wave gate failure MUST block the next wave — caller enforces this.
        """
        gates_file = self.config.workspace_dir / "forge.gates.sh"
        if not gates_file.exists():
            print(
                f"[ORCHESTRATOR] Post-wave gate: no gates file at {gates_file} — skipping.",
                flush=True,
            )
            return True  # No gate file → vacuous pass

        print("[ORCHESTRATOR] Running post-wave integration gate…", flush=True)
        result = run_quality_gates(
            self.config.repo_root,
            gates_file,
            dict(os.environ),
            cwd=self.config.repo_root,
        )
        passed = result.returncode == 0
        if passed:
            print("[ORCHESTRATOR] Post-wave gate: PASS", flush=True)
        else:
            print(
                f"[ORCHESTRATOR] Post-wave gate: FAIL (exit {result.returncode}) "
                "— next wave blocked.",
                flush=True,
            )
        return passed

    # ── Main run loop ─────────────────────────────────────────────────────────

    def run(self) -> OrchestratorResult:
        """Run the TagTeam orchestration loop.

        Returns OrchestratorResult with final status.

        Entry safety gate: aborts if check-plan has unreviewed confidence-low
        items (must call validate_plan() first and handle low_confidence_items()
        externally before calling run()).
        """
        # Start ghost-sweep daemon
        stop_event = threading.Event()
        sweep_thread = threading.Thread(
            target=self.coordinator.run_ghost_sweep_loop,
            args=(self.ghost_sweep_tick, stop_event),
            daemon=True,
        )
        sweep_thread.start()

        try:
            return self._run_loop()
        finally:
            stop_event.set()
            # Terminate any still-running agent processes on abnormal exit
            for slot in list(self._active_slots.values()):
                try:
                    slot.process.terminate()
                except Exception:
                    pass

    def _run_loop(self) -> OrchestratorResult:
        completed: list[str] = []
        blocked: list[str] = []
        wave_number = 0
        # Stories dispatched in the current wave
        wave_active: set[str] = set()

        while True:
            # ── Fill idle slots ───────────────────────────────────────────────
            queue = self.ready_queue()
            free_slots = [
                i for i in range(self.max_agents) if i not in self._active_slots
            ]

            for slot_idx in free_slots:
                if not queue:
                    break
                story_id = queue.pop(0)
                backend = self._pick_backend(story_id)
                if backend is None:
                    # Retry budget exhausted → permanently blocked
                    blocked.append(story_id)
                    self._tried_backends.setdefault(story_id, []).append("__blocked__")
                    print(
                        f"[ORCHESTRATOR] Story {story_id!r} blocked after "
                        f"{self._MAX_RETRIES} backend retries.",
                        flush=True,
                    )
                    continue

                print(
                    f"[ORCHESTRATOR] Dispatching {story_id!r} → slot {slot_idx} "
                    f"(backend={backend})",
                    flush=True,
                )
                try:
                    slot = self.dispatch(story_id, slot_idx, backend)
                    self._active_slots[slot_idx] = slot
                    wave_active.add(story_id)
                except OrchestratorError as exc:
                    print(f"[ORCHESTRATOR] Dispatch failed: {exc}", flush=True)
                    # Return backend attempt token so retries still work
                    backends = self._tried_backends.get(story_id, [])
                    if backends and backends[-1] == backend:
                        backends.pop()

            # ── Check for wave completion ─────────────────────────────────────
            if not self._active_slots:
                fresh_queue = self.ready_queue()

                if not fresh_queue and not wave_active:
                    # Nothing running, nothing queued — check overall completion
                    with open(self.prd_file) as fh:
                        prd: dict[str, Any] = json.load(fh)
                    remaining = [
                        s["id"]
                        for s in prd.get("userStories", [])
                        if not s.get("passes", False)
                    ]
                    # Filter out permanently blocked stories
                    perm_blocked = {
                        sid
                        for sid, attempts in self._tried_backends.items()
                        if "__blocked__" in attempts
                    }
                    actionable = [r for r in remaining if r not in perm_blocked]
                    if not actionable:
                        # All done or all blocked
                        return OrchestratorResult(
                            status="complete" if not blocked else "blocked",
                            stories_completed=completed,
                            stories_blocked=blocked,
                        )
                    # Progress stalled — some stories remain but none are ready
                    # (dep chain not satisfied or all blocked)
                    return OrchestratorResult(
                        status="blocked",
                        stories_completed=completed,
                        stories_blocked=blocked + actionable,
                    )

                # Wave boundary: we dispatched some stories and they all finished.
                # Run post-wave integration gate before next wave.
                if wave_active:
                    wave_number += 1
                    print(
                        f"[ORCHESTRATOR] Wave {wave_number} complete "
                        f"({len(wave_active)} stories). Running post-wave gate…",
                        flush=True,
                    )
                    if not self._run_post_wave_gate():
                        return OrchestratorResult(
                            status="gate_failed",
                            stories_completed=completed,
                            stories_blocked=blocked,
                            gate_failure_wave=wave_number,
                        )
                    wave_active = set()

                # Loop back to fill slots with fresh queue
                continue

            # ── Poll active slots ─────────────────────────────────────────────
            time.sleep(self.poll_interval)

            completed_this_tick: list[SlotState] = []
            failed_this_tick: list[SlotState] = []

            for slot in list(self._active_slots.values()):
                retcode = slot.process.poll()
                if retcode is None:
                    continue  # Still running
                if retcode == 0:
                    completed_this_tick.append(slot)
                else:
                    failed_this_tick.append(slot)

            for slot in completed_this_tick:
                print(
                    f"[ORCHESTRATOR] Story {slot.story_id!r} completed on slot {slot.slot_idx}.",
                    flush=True,
                )
                self.on_story_complete(slot)
                completed.append(slot.story_id)

            for slot in failed_this_tick:
                print(
                    f"[ORCHESTRATOR] Story {slot.story_id!r} failed on slot {slot.slot_idx} "
                    f"(exit {slot.process.returncode}) — will retry.",
                    flush=True,
                )
                self.on_story_fail(slot)
                # Remove from wave_active so the retry is counted in the next wave
                wave_active.discard(slot.story_id)


# ── Python command helper ─────────────────────────────────────────────────────


def _python_cmd() -> str:
    """Return the Python executable path (venv-aware)."""
    script_dir = Path(__file__).resolve().parents[2]
    venv_python = script_dir / ".venv" / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    return "python3"


# ── Interactive check-plan gate ───────────────────────────────────────────────


def _interactive_low_confidence_review(
    low_conf_items: list[dict[str, Any]],
    plan_file: Path,
) -> bool:
    """Pause for human review of low-confidence dependency items.

    When stdin is a TTY: presents each item with a three-choice prompt:
      [c]onfirm   — accept the dependency as-is (default)
      [o]verride  — remove the dependency (treat stories as independent)
      [s]equential-only — keep the dependency; acknowledge it is a safe
                          sequential ordering constraint, not a hard API contract

    When stdin is NOT a TTY (e.g., CI without FORGE_TAGTEAM_AUTO_CONFIRM):
      Prints the offending items and returns False (abort).

    Args:
        low_conf_items: Plan story dicts with confidence == 'low'.
        plan_file:      Path to tagteam.plan.json (written back on override).

    Returns:
        True  → proceed with the Orchestrator run.
        False → abort; caller should return exit code 1.
    """
    import sys

    if not sys.stdin.isatty():
        print(
            "[ORCHESTRATOR] ABORT: plan contains unreviewed low-confidence "
            "dependency items and stdin is not a TTY (non-interactive mode).\n"
            + "\n".join(
                f"  - {s['storyId']}: {s.get('rationale', '')}"
                for s in low_conf_items
            )
            + "\n\nSet FORGE_TAGTEAM_AUTO_CONFIRM=true to treat low-confidence "
            "items as sequential-only in CI mode, or run interactively to review.",
            flush=True,
        )
        return False

    print(
        "\n[CHECK-PLAN] The following dependency items have low confidence "
        "and require human review:\n",
        flush=True,
    )

    # Load plan so we can patch overrides back
    with open(plan_file) as fh:
        plan: dict[str, Any] = json.load(fh)

    plan_index: dict[str, dict[str, Any]] = {
        s["storyId"]: s for s in plan.get("stories", [])
    }

    overrides: list[tuple[str, list[str]]] = []  # (storyId, depsToRemove)

    for item in low_conf_items:
        sid: str = item["storyId"]
        deps: list[str] = item.get("dependsOn", [])
        rationale: str = item.get("rationale", "(no rationale)")
        print(
            f"  Story : {sid}\n"
            f"  DepsOn: {', '.join(deps) if deps else '(none)'}\n"
            f"  Reason: {rationale}\n",
            flush=True,
        )
        while True:
            try:
                choice = input(
                    "  Choice — [c]onfirm / [o]verride (remove dep) / "
                    "[s]equential-only (keep, treat as ordering constraint): "
                ).strip().lower()
            except EOFError:
                choice = "s"

            if choice in ("c", "confirm", ""):
                print(f"  → confirmed for {sid}", flush=True)
                break
            elif choice in ("o", "override"):
                overrides.append((sid, deps[:]))
                print(
                    f"  → dependency on {deps} removed for {sid} "
                    "(will run in parallel)",
                    flush=True,
                )
                break
            elif choice in ("s", "sequential-only", "sequential"):
                print(
                    f"  → sequential-only for {sid} "
                    "(dependency kept; treated as ordering constraint)",
                    flush=True,
                )
                break
            else:
                print("  Invalid choice — enter c, o, or s.", flush=True)

        print()

    # Apply overrides: remove deps from plan and write back
    if overrides:
        for sid, removed_deps in overrides:
            if sid in plan_index:
                current_deps: list[str] = plan_index[sid].get("dependsOn", [])
                plan_index[sid]["dependsOn"] = [
                    d for d in current_deps if d not in removed_deps
                ]

        with open(plan_file, "w") as fh:
            json.dump(plan, fh, indent=2)
            fh.write("\n")
        print(
            f"[CHECK-PLAN] Plan updated: removed {len(overrides)} "
            "low-confidence dep(s).",
            flush=True,
        )

    print("[CHECK-PLAN] Review complete — proceeding.\n", flush=True)
    return True


# ── Public entry point ────────────────────────────────────────────────────────


def run_orchestrator(
    config: ForgeConfig,
    max_agents: int,
    plan_file: Path | None = None,
    prd_file: Path | None = None,
    *,
    skip_low_confidence_check: bool = False,
) -> int:
    """Entry point called from forge/cli.py.

    Args:
        config:                   Resolved ForgeConfig.
        max_agents:               Number of parallel agent slots.
        plan_file:                Override plan file path (default: tagteam.plan.json).
        prd_file:                 Override prd.json path (default: config.prd_file).
        skip_low_confidence_check: If True, treat low-confidence items as already
                                  reviewed (CI / FORGE_TAGTEAM_AUTO_CONFIRM).

    Returns:
        Exit code: 0 on complete, 1 on gate_failed/blocked/error.
    """
    resolved_plan = (plan_file or (config.repo_root / "tagteam.plan.json")).resolve()
    resolved_prd = (prd_file or config.prd_file).resolve()
    db_path = config.workspace_dir / "forge-memory.db"
    session_id = os.environ.get("FORGE_SESSION_ID", "tagteam-adhoc")

    # Validate plan file exists
    if not resolved_plan.exists():
        print(
            f"[ORCHESTRATOR] ERROR: tagteam.plan.json not found at {resolved_plan}\n"
            "Run 'forge plan' first to generate the dependency plan.",
            flush=True,
        )
        return 1

    coordinator = Coordinator(
        db_path=db_path,
        prd_file=resolved_prd,
        session_id=session_id,
        agent_timeout=config.agent_timeout_seconds or 600.0,
    )

    worktree_manager = WorktreeManager(
        repo_root=config.repo_root,
        db_path=db_path,
        session_id=session_id,
    )

    # ── Safety gate: validate plan and check confidence-low items ─────────────
    topo = coordinator.validate_plan(resolved_plan, resolved_prd)
    if topo.has_cycle:
        print(
            f"[ORCHESTRATOR] ABORT: DAG cycle detected.\n{topo.error}",
            flush=True,
        )
        return 1
    if topo.phantom_ids:
        print(
            f"[ORCHESTRATOR] ABORT: phantom story IDs in plan: "
            + ", ".join(topo.phantom_ids),
            flush=True,
        )
        return 1

    if not skip_low_confidence_check:
        low_conf = coordinator.low_confidence_items(resolved_plan)
        if low_conf:
            if not _interactive_low_confidence_review(low_conf, resolved_plan):
                return 1

    orchestrator = Orchestrator(
        config=config,
        max_agents=max_agents,
        coordinator=coordinator,
        worktree_manager=worktree_manager,
        plan_file=resolved_plan,
        prd_file=resolved_prd,
    )

    result = orchestrator.run()

    print(
        f"[ORCHESTRATOR] Done. status={result.status} "
        f"completed={len(result.stories_completed)} "
        f"blocked={len(result.stories_blocked)}",
        flush=True,
    )
    if result.stories_completed:
        print("[ORCHESTRATOR] Completed: " + ", ".join(result.stories_completed))
    if result.stories_blocked:
        print("[ORCHESTRATOR] Blocked: " + ", ".join(result.stories_blocked))

    return 0 if result.status == "complete" else 1
