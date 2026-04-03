# Co-authored by FORGE (Session: forge-20260402130446-4010642)
"""TagTeam plan schema — TypedDict definitions for tagteam.plan.json."""
from __future__ import annotations

from typing import Literal, TypedDict


class StoryPlan(TypedDict):
    """Dependency and interface record for one PRD story."""

    storyId: str
    dependsOn: list[str]
    interfacesProduced: list[str]
    interfacesConsumed: list[str]
    confidence: Literal["high", "medium", "low"]
    rationale: str


class TagTeamPlan(TypedDict):
    """Root structure of tagteam.plan.json."""

    version: str
    generatedAt: str
    rerun: bool
    sourceFilesConsulted: list[str]
    stories: list[StoryPlan]
