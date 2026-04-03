# Co-authored by FORGE (Session: forge-20260402183630-43092)
"""TagTeam — parallel-agent orchestration modules for ForgeMP.

Public entry point:
    run_tagteam(config, max_agents) — wire all TagTeam modules and run.

All other modules (Planner, Coordinator, WorktreeManager, Orchestrator,
Resolver) are internal and should not be imported directly by callers.
"""
from __future__ import annotations

from ..models import ForgeConfig
from .orchestrator import run_orchestrator


def run_tagteam(config: ForgeConfig, max_agents: int) -> int:
    """Single public entry point for TagTeam parallel execution.

    Runs the full TagTeam flow:
      1. Validate plan (must already exist — run ``forge plan`` first).
      2. Check-plan governance gate (hard failures + interactive soft gate).
      3. Orchestrator wave loop with post-wave integration gate.

    Args:
        config:     Resolved ForgeConfig (repo_root, workspace_dir, prd_file, …).
        max_agents: Maximum concurrent agent slots.  1 is valid (degenerate case:
                    Planner ran, single-agent sequential pool, no parallelism).

    Returns:
        Exit code: 0 on complete, 1 on gate_failed / blocked / plan error.
    """
    return run_orchestrator(config=config, max_agents=max_agents)
