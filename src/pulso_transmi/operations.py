"""Validation helpers shared by the production forecast operation."""

from __future__ import annotations

import math

import pandas as pd

MIN_HISTORY_DAYS = 28


def history_covers_cycle(history: pd.DataFrame | None, cutoff: pd.Timestamp, station_ids: set[str]) -> bool:
    """Require a dense recent window before trusting the cache for inference."""
    if history is None or history.empty or not station_ids:
        return False
    cutoff = pd.Timestamp(cutoff).floor("15min")
    window_start = cutoff - pd.Timedelta(days=MIN_HISTORY_DAYS)
    expected = pd.date_range(window_start, cutoff, freq="15min", tz="UTC")
    recent = history[history["observed_at"].between(window_start, cutoff)]
    for station in station_ids:
        timestamps = pd.DatetimeIndex(recent.loc[recent["station_id"].astype(str) == station, "observed_at"].unique())
        coverage = timestamps.intersection(expected).size / len(expected)
        if coverage < 0.98 or cutoff not in timestamps:
            return False
    return True


def validate_prediction_batch(targets: list[dict], values: list[float]) -> None:
    """Fail closed instead of submitting a misaligned or invalid batch."""
    if len(targets) != len(values) or not targets:
        raise RuntimeError(f"Prediction count mismatch: {len(values)} for {len(targets)} targets")
    keys = [(str(target["station_id"]), pd.Timestamp(target["target_at"])) for target in targets]
    if len(set(keys)) != len(keys):
        raise RuntimeError("Forecast cycle contains duplicate station/timestamp targets")
    if not all(math.isfinite(float(value)) and float(value) >= 0 for value in values):
        raise RuntimeError("Model produced a non-finite or negative prediction")
