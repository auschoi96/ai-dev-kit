"""GEPA-powered skill optimization using optimize_anything API.

Public API:
    optimize_skill()              - End-to-end optimize a SKILL.md (and optionally tools)
    OptimizationResult            - Dataclass with optimization results
    PRESETS                       - GEPA config presets (quick, standard, thorough)
"""

from .runner import optimize_skill, OptimizationResult
from .config import PRESETS
from .review import review_optimization, apply_optimization

__all__ = [
    "optimize_skill",
    "OptimizationResult",
    "PRESETS",
    "review_optimization",
    "apply_optimization",
]
