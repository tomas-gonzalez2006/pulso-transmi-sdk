import pandas as pd

import pytest

from src.pipeline import history_covers_cycle, validate_prediction_batch


def test_incomplete_cached_history_is_rejected() -> None:
    cutoff = pd.Timestamp("2026-01-08T00:00:00Z")
    timestamps = pd.date_range(cutoff - pd.Timedelta(days=28), cutoff, freq="15min", tz="UTC")
    history = pd.DataFrame(
        {
            "station_id": ["A"] * len(timestamps),
            "observed_at": timestamps,
            "demand": 10,
        }
    )

    assert not history_covers_cycle(history, cutoff, {"A", "B"})


def test_complete_cached_history_is_accepted() -> None:
    cutoff = pd.Timestamp("2026-01-08T00:00:00Z")
    timestamps = pd.date_range(cutoff - pd.Timedelta(days=28), cutoff, freq="15min", tz="UTC")
    history = pd.DataFrame(
        {
            "station_id": [station for station in ("A", "B") for _ in timestamps],
            "observed_at": list(timestamps) * 2,
            "demand": 10,
        }
    )

    assert history_covers_cycle(history, cutoff, {"A", "B"})


def test_prediction_batch_rejects_invalid_values() -> None:
    targets = [{"station_id": "A", "target_at": "2026-01-08T00:15:00Z"}]
    with pytest.raises(RuntimeError):
        validate_prediction_batch(targets, [float("nan")])
