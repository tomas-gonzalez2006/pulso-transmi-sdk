"""Helpers for normalizing official leaderboard metrics."""
from __future__ import annotations

from typing import Any


def _fraction(value: Any) -> float | None:
    if value is None:
        return None
    number = float(value)
    return number / 100 if number > 1 else number


def build_metric(student: str, row: dict[str, Any]) -> dict[str, Any]:
    """Convert one leaderboard row to the ``model_metrics`` schema."""
    accuracy = float(row["accuracy"])
    if accuracy <= 1:
        accuracy *= 100
    wape = row.get("wape")
    if wape is None:
        wape = max(0.0, 1.0 - accuracy / 100.0)
    else:
        wape = _fraction(wape)
    rank = row.get("rank")
    return {
        "model_version": "pulso-leaderboard:cumulative",
        "metric_scope": "cumulative",
        "accuracy": accuracy,
        "wape": wape,
        "coverage": _fraction(row.get("coverage")),
        "reference_window": "leaderboard:cumulative",
        "metadata": {
            "student": student,
            "leaderboard_window": "cumulative",
            "rank": int(rank) if rank is not None else None,
            "source_row": row,
        },
    }
