"""Evidence-bounded coaching for Token Meter."""

from .contracts import (
    GOAL_METRICS,
    GOAL_RUNTIMES,
    RECOMMENDATIONS,
    goal_progress,
    metric_snapshot,
    normalize_goal,
)

__all__ = [
    "GOAL_METRICS",
    "GOAL_RUNTIMES",
    "RECOMMENDATIONS",
    "goal_progress",
    "metric_snapshot",
    "normalize_goal",
]
