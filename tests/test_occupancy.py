import numpy as np
import pandas as pd
import importlib.util
from pathlib import Path

from pulso_transmi.occupancy import build_training_frame, future_features


_TRAIN_SCRIPT = Path(__file__).parents[1] / "scripts" / "train_and_version.py"
_SPEC = importlib.util.spec_from_file_location("train_and_version", _TRAIN_SCRIPT)
assert _SPEC and _SPEC.loader
train_and_version = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(train_and_version)


class _Trained:
    station_codes = {"A": 0}
    seasonal_means = {}
    fallback_seasonal = 10.0
    last_context = {
        "rain_mm": 0.0,
        "rain_forecast": 0.0,
        "temperature_c": 18.0,
        "temperature_forecast": 18.0,
        "event_intensity": 0.0,
    }


def test_future_network_lags_do_not_turn_into_zero_after_cutoff() -> None:
    observed = pd.date_range("2026-01-01 12:00", periods=2, freq="15min", tz="UTC")
    history = pd.DataFrame(
        {
            "station_id": ["A", "A"],
            "observed_at": observed,
            "demand": [20.0, 25.0],
        }
    )
    context = pd.DataFrame(
        {
            "observed_at": observed,
            "rain_mm": [0.0, 0.0],
            "rain_forecast": [0.0, 0.0],
            "temperature_c": [18.0, 18.0],
            "temperature_forecast": [18.0, 18.0],
            "event_intensity": [0.0, 0.0],
        }
    )
    target = [{"station_id": "A", "target_at": "2026-01-01T12:30:00Z"}]

    frame = future_features(history, context, target, _Trained(), observed[-1])

    assert np.isnan(frame.loc[0, "network_total_lag_4"])
    assert frame.loc[0, "network_total_lag_1"] == 25.0


def test_training_lags_use_timestamps_not_previous_row() -> None:
    observed = pd.to_datetime(
        ["2026-01-01T12:00:00Z", "2026-01-01T12:30:00Z", "2026-01-01T12:45:00Z"],
        utc=True,
    )
    history = pd.DataFrame({"station_id": ["A"] * 3, "observed_at": observed, "demand": [20.0, 30.0, 40.0]})
    context = pd.DataFrame(
        {
            "observed_at": observed,
            "rain_mm": 0.0,
            "rain_forecast": 0.0,
            "temperature_c": 18.0,
            "temperature_forecast": 18.0,
            "event_intensity": 0.0,
        }
    )

    frame, _ = build_training_frame(history, context)

    # 12:30 has no 12:15 observation, so its lag_1 must not use 20.0.
    row = frame.loc[frame["observed_at"] == observed[1]].iloc[0]
    assert np.isnan(row["lag_1"])


def test_retraining_evaluates_ten_unique_candidates() -> None:
    names = [name for name, _, _ in train_and_version.CANDIDATES]

    assert len(names) == 10
    assert len(set(names)) == len(names)


def test_training_cutoff_excludes_last_two_records_per_station() -> None:
    observed = pd.date_range("2026-01-01 12:00", periods=4, freq="15min", tz="UTC")
    observations = pd.DataFrame(
        {
            "station_id": ["A"] * 4 + ["B"] * 4,
            "observed_at": list(observed) * 2,
            "demand": 1.0,
        }
    )

    assert train_and_version.training_cutoff_for_last_two(observations) == observed[1]
