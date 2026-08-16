"""Recherche über mehrere Quellen, mit Belegen."""

from jarvis.research.base import (
    Finding,
    ResearchProvider,
    ResearchQuery,
    ResearchResponse,
    Source,
)
from jarvis.research.evaluator import Conflict, Evaluator, Evidence
from jarvis.research.pipeline import ResearchPipeline, render_evidence
from jarvis.research.tools import ResearchTool

__all__ = [
    "Conflict",
    "Evaluator",
    "Evidence",
    "Finding",
    "ResearchPipeline",
    "ResearchProvider",
    "ResearchQuery",
    "ResearchResponse",
    "ResearchTool",
    "Source",
    "render_evidence",
]
