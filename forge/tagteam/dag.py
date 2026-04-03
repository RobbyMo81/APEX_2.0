# Co-authored by FORGE (Session: forge-20260402130446-4010642)
"""TagTeam DAG validation — Kahn's algorithm for topological sort and cycle detection.

Complexity: O(V+E) where V = story count, E = total dependsOn edges.

Usage:
    from forge.tagteam.dag import validate_dag, TopologicalOrder

    result = validate_dag(plan_stories, prd_story_ids)
    if result.has_cycle:
        print(result.error)  # clear message naming the cycle
    else:
        print(result.order)  # topological processing order
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TopologicalOrder:
    """Result of DAG validation."""

    order: list[str] = field(default_factory=list)
    """Story IDs in valid topological (execution) order."""

    has_cycle: bool = False
    """True when a circular dependency was detected — hard failure."""

    error: str = ""
    """Human-readable error message on failure; empty on success."""

    phantom_ids: list[str] = field(default_factory=list)
    """dependsOn IDs that do not exist in prd.json."""


def validate_dag(
    plan_stories: list[dict[str, Any]],
    prd_story_ids: set[str],
) -> TopologicalOrder:
    """Run Kahn's algorithm on the plan dependency graph.

    Args:
        plan_stories: List of story dicts from tagteam.plan.json, each with
                      ``storyId`` and ``dependsOn`` fields.
        prd_story_ids: Set of all valid story IDs from prd.json.

    Returns:
        TopologicalOrder — inspect `has_cycle`, `phantom_ids`, and `order`.

    Algorithm:
        1. Build adjacency list and in-degree map for all nodes.
        2. Seed the processing queue with zero-in-degree nodes.
        3. Pop each node, emit it, decrement in-degree of its successors,
           re-queue any successor whose in-degree reaches zero.
        4. If the queue empties with unprocessed nodes remaining → cycle.
    """
    # --- Phase 1: collect all node IDs and validate against prd.json ---
    plan_ids: set[str] = {s["storyId"] for s in plan_stories}
    phantom_ids: list[str] = []

    # Build edges: {story_id -> list[successor_id]}
    # An edge A→B means "A must complete before B" (B dependsOn A).
    adjacency: dict[str, list[str]] = {sid: [] for sid in plan_ids}
    in_degree: dict[str, int] = {sid: 0 for sid in plan_ids}

    for story in plan_stories:
        sid = story["storyId"]
        for dep in story.get("dependsOn", []):
            if dep not in prd_story_ids:
                phantom_ids.append(f"{sid}→{dep}")
                continue
            if dep not in adjacency:
                # dep exists in prd.json but not in plan — treat as external,
                # add as a node with no outgoing edges
                adjacency[dep] = []
                in_degree[dep] = 0
            adjacency[dep].append(sid)
            in_degree[sid] += 1

    if phantom_ids:
        return TopologicalOrder(
            has_cycle=False,
            phantom_ids=phantom_ids,
            error=(
                "dependsOn references IDs not in prd.json: "
                + ", ".join(phantom_ids)
            ),
        )

    # --- Phase 2: Kahn's BFS ---
    queue: list[str] = [sid for sid, deg in in_degree.items() if deg == 0]
    order: list[str] = []

    while queue:
        node = queue.pop(0)
        order.append(node)
        for successor in adjacency.get(node, []):
            in_degree[successor] -= 1
            if in_degree[successor] == 0:
                queue.append(successor)

    total_nodes = len(adjacency)
    if len(order) < total_nodes:
        # Unprocessed nodes remain — at least one cycle exists
        cycle_nodes = [sid for sid in adjacency if sid not in set(order)]
        return TopologicalOrder(
            has_cycle=True,
            order=order,
            error=(
                "Circular dependency detected among stories: "
                + ", ".join(sorted(cycle_nodes))
                + " — aborting"
            ),
        )

    return TopologicalOrder(order=order)
