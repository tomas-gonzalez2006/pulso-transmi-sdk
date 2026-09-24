"""Feature engineering and XGBoost inference shared by evaluation and delivery."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
from xgboost import XGBRegressor

FEATURES = [
    "network_total_lag_1", "network_total_lag_4", "network_total_lag_96", "network_total_lag_672",
    "rain_mm", "rain_forecast", "temperature_c", "temperature_forecast", "event_intensity",
    "lag_1", "lag_2", "lag_4", "lag_96", "lag_192", "lag_672",
    "mean_4", "mean_96", "mean_672", "slot", "minute_sin", "minute_cos",
    "weekday_sin", "weekday_cos", "station_code", "seasonal_mean",
]
CONTEXT_COLUMNS = ["rain_mm", "rain_forecast", "temperature_c", "temperature_forecast", "event_intensity"]


@dataclass
class OccupancyModel:
    model: XGBRegressor
    station_codes: dict[str, int]
    seasonal_means: dict[tuple[str, int], float]
    fallback_seasonal: float
    last_context: dict[str, float]


def _temporal(data: pd.DataFrame) -> pd.DataFrame:
    local = data["observed_at"].dt.tz_convert("America/Bogota")
    minute = local.dt.hour * 60 + local.dt.minute
    data["slot"] = local.dt.dayofweek * 96 + local.dt.hour * 4 + local.dt.minute // 15
    data["minute_sin"] = np.sin(2 * np.pi * minute / 1440)
    data["minute_cos"] = np.cos(2 * np.pi * minute / 1440)
    data["weekday_sin"] = np.sin(2 * np.pi * local.dt.dayofweek / 7)
    data["weekday_cos"] = np.cos(2 * np.pi * local.dt.dayofweek / 7)
    return data


def build_training_frame(history: pd.DataFrame, context: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    data = history.sort_values(["station_id", "observed_at"]).copy()
    data["station_id"] = data["station_id"].astype(str)
    totals = data.groupby("observed_at")["demand"].sum()
    for lag in (1, 4, 96, 672):
        data[f"network_total_lag_{lag}"] = totals.reindex(data["observed_at"] - pd.to_timedelta(15 * lag, unit="m")).to_numpy()
    data = data.merge(context, on="observed_at", how="left")
    group = data.groupby("station_id", sort=False)["demand"]
    for lag in (1, 2, 4, 96, 192, 672):
        data[f"lag_{lag}"] = group.shift(lag)
    for window in (4, 96, 672):
        data[f"mean_{window}"] = group.transform(lambda values: values.shift(1).rolling(window, min_periods=window).mean())
    station_codes = {station: index for index, station in enumerate(sorted(data["station_id"].unique()))}
    data["station_code"] = data["station_id"].map(station_codes)
    return _temporal(data), station_codes


def train(history: pd.DataFrame, context: pd.DataFrame) -> OccupancyModel:
    frame, station_codes = build_training_frame(history, context)
    train_frame = frame.dropna(subset=[column for column in FEATURES if column != "seasonal_mean"] + ["demand"]).copy()
    seasonal = train_frame.groupby(["station_id", "slot"])["demand"].mean()
    fallback = float(train_frame["demand"].mean())
    train_frame["seasonal_mean"] = [float(seasonal.get((s, slot), fallback)) for s, slot in zip(train_frame["station_id"], train_frame["slot"], strict=True)]
    model = XGBRegressor(n_estimators=400, max_depth=5, learning_rate=0.05, min_child_weight=5, subsample=0.85, colsample_bytree=0.7, reg_lambda=5.0, objective="reg:squarederror", tree_method="hist", n_jobs=-1, random_state=42)
    model.fit(train_frame[FEATURES], train_frame["demand"])
    known_context = context.sort_values("observed_at").iloc[-1]
    last_context = {column: float(known_context[column]) for column in CONTEXT_COLUMNS}
    seasonal_means = {(str(station), int(slot)): float(value) for (station, slot), value in seasonal.items()}
    return OccupancyModel(model, station_codes, seasonal_means, fallback, last_context)


def future_features(history: pd.DataFrame, context: pd.DataFrame, targets: list[dict], trained: OccupancyModel, cutoff: pd.Timestamp) -> pd.DataFrame:
    history = history.copy(); history["station_id"] = history["station_id"].astype(str)
    history["observed_at"] = pd.to_datetime(history["observed_at"], utc=True)
    totals = history.groupby("observed_at")["demand"].sum()
    indexed = history.set_index(["station_id", "observed_at"])["demand"]
    rows = []
    for target in targets:
        station = str(target["station_id"]); timestamp = pd.Timestamp(target["target_at"])
        local = timestamp.tz_convert("America/Bogota"); minute = local.hour * 60 + local.minute; slot = local.dayofweek * 96 + local.hour * 4 + local.minute // 15
        row: dict[str, float | int] = {"station_code": trained.station_codes.get(station, -1), "slot": slot, "minute_sin": math.sin(2 * math.pi * minute / 1440), "minute_cos": math.cos(2 * math.pi * minute / 1440), "weekday_sin": math.sin(2 * math.pi * local.dayofweek / 7), "weekday_cos": math.cos(2 * math.pi * local.dayofweek / 7)}
        for lag in (1, 4, 96, 672): row[f"network_total_lag_{lag}"] = float(totals.get(timestamp - pd.Timedelta(minutes=15 * lag), 0))
        for column in CONTEXT_COLUMNS: row[column] = trained.last_context[column]
        for lag in (1, 2, 4, 96, 192, 672): row[f"lag_{lag}"] = float(indexed.get((station, timestamp - pd.Timedelta(minutes=15 * lag)), np.nan))
        values = [indexed.get((station, timestamp - pd.Timedelta(minutes=15 * lag)), np.nan) for lag in range(1, 5)]
        row["mean_4"] = float(np.nanmean(values))
        values = [indexed.get((station, timestamp - pd.Timedelta(minutes=15 * lag)), np.nan) for lag in range(1, 97)]
        row["mean_96"] = float(np.nanmean(values))
        values = [indexed.get((station, timestamp - pd.Timedelta(minutes=15 * lag)), np.nan) for lag in range(1, 673)]
        row["mean_672"] = float(np.nanmean(values))
        row["seasonal_mean"] = trained.seasonal_means.get((station, slot), trained.fallback_seasonal)
        rows.append(row)
    return pd.DataFrame(rows, columns=FEATURES)


def recursive_predict(history: pd.DataFrame, context: pd.DataFrame, targets: list[dict], trained: OccupancyModel, cutoff: pd.Timestamp) -> list[float]:
    """Forecast multiple horizons without using unavailable future observations."""
    working = history[["station_id", "observed_at", "demand"]].copy()
    working["station_id"] = working["station_id"].astype(str)
    working["observed_at"] = pd.to_datetime(working["observed_at"], utc=True)
    ordered = sorted(enumerate(targets), key=lambda item: (pd.Timestamp(item[1]["target_at"]), str(item[1]["station_id"])))
    predictions: dict[int, float] = {}
    for index, target in ordered:
        features = future_features(working, context, [target], trained, cutoff)
        value = max(0.0, float(trained.model.predict(features)[0]))
        predictions[index] = round(value, 3)
        working = pd.concat([working, pd.DataFrame([{
            "station_id": str(target["station_id"]),
            "observed_at": pd.Timestamp(target["target_at"]),
            "demand": value,
        }])], ignore_index=True)
    return [predictions[index] for index in range(len(targets))]
