"""Temporal backtest for a station occupancy (demand) forecasting model."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import joblib
from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import Ridge
from xgboost import XGBRegressor


API = "https://pulso-transmi.72-60-245-2.sslip.io"


def accuracy_by_station(actual: pd.Series, predicted: pd.Series, station: pd.Series) -> pd.Series:
    errors = (actual - predicted).abs()
    return 100 * (1 - errors.groupby(station).sum() / actual.groupby(station).sum()).clip(lower=0)


def features(frame: pd.DataFrame, context: pd.DataFrame) -> pd.DataFrame:
    frame = frame.sort_values(["station_id", "observed_at"]).copy()
    totals = frame.groupby("observed_at")["demand"].sum()
    for lag in (1, 4, 96, 672):
        frame[f"network_total_lag_{lag}"] = [totals.get(timestamp - pd.Timedelta(minutes=15 * lag), np.nan) for timestamp in frame["observed_at"]]
    frame = frame.merge(context, on="observed_at", how="left")
    group = frame.groupby("station_id", sort=False)["demand"]
    for lag in (1, 2, 4, 96, 192, 672):
        frame[f"lag_{lag}"] = group.shift(lag)
    for window in (4, 96, 672):
        frame[f"mean_{window}"] = group.transform(lambda values: values.shift(1).rolling(window, min_periods=window).mean())

    local = frame["observed_at"].dt.tz_convert("America/Bogota")
    minute = local.dt.hour * 60 + local.dt.minute
    frame["slot"] = local.dt.dayofweek * 96 + local.dt.hour * 4 + local.dt.minute // 15
    frame["minute_sin"] = np.sin(2 * np.pi * minute / 1440)
    frame["minute_cos"] = np.cos(2 * np.pi * minute / 1440)
    frame["weekday_sin"] = np.sin(2 * np.pi * local.dt.dayofweek / 7)
    frame["weekday_cos"] = np.cos(2 * np.pi * local.dt.dayofweek / 7)
    frame["station_code"] = frame["station_id"].astype("category").cat.codes
    return frame


def main() -> None:
    observations = pd.read_csv(f"{API}/v1/downloads/observations.csv", parse_dates=["observed_at"], dtype={"station_id": "string"})
    context = pd.read_csv(f"{API}/v1/downloads/context.csv", parse_dates=["observed_at"])
    frame = features(observations, context)
    cutoff = frame["observed_at"].max() - pd.Timedelta(days=7)
    train = frame[frame["observed_at"] <= cutoff].dropna().copy()
    validation = frame[frame["observed_at"] > cutoff].dropna().copy()
    local_train = train["observed_at"].dt.tz_convert("America/Bogota")
    local_validation = validation["observed_at"].dt.tz_convert("America/Bogota")
    seasonal_means = train.groupby(["station_id", "slot"])["demand"].mean()
    train["seasonal_mean"] = [seasonal_means.get((station, slot), train["demand"].mean()) for station, slot in zip(train["station_id"], train["slot"])]
    validation["seasonal_mean"] = [seasonal_means.get((station, slot), train["demand"].mean()) for station, slot in zip(validation["station_id"], validation["slot"])]
    feature_columns = [column for column in frame.columns if column not in {"observed_at", "station_id", "demand"}]
    feature_columns.append("seasonal_mean")

    candidates = {
        "xgboost": XGBRegressor(
            n_estimators=400,
            max_depth=5,
            learning_rate=0.05,
            min_child_weight=5,
            subsample=0.85,
            colsample_bytree=0.7,
            reg_lambda=5.0,
            objective="reg:squarederror",
            tree_method="hist",
            n_jobs=-1,
            random_state=42,
        ),
    }
    model_scores = {}
    predictions = {}
    for name, model in candidates.items():
        model.fit(train[feature_columns], train["demand"])
        prediction = np.maximum(0, model.predict(validation[feature_columns]))
        predictions[name] = prediction
        model_scores[name] = round(float(100 * (1 - np.abs(validation["demand"].to_numpy() - prediction).sum() / validation["demand"].sum())), 4)
    best_name = max(model_scores, key=model_scores.get)
    validation["prediction"] = predictions[best_name]
    artifacts = Path("artifacts")
    artifacts.mkdir(exist_ok=True)
    joblib.dump(candidates[best_name], artifacts / "occupancy_model.joblib")
    (artifacts / "occupancy_model_metadata.json").write_text(json.dumps({"model": best_name, "features": feature_columns, "cutoff": cutoff.isoformat()}, indent=2), encoding="utf-8")
    scores = accuracy_by_station(validation["demand"], validation["prediction"], validation["station_id"])
    overall = 100 * (1 - (validation["demand"] - validation["prediction"]).abs().sum() / validation["demand"].sum())
    official_accuracy = float(scores.mean())
    baseline = validation["lag_96"].to_numpy()
    weekly = validation["lag_672"].to_numpy()
    seasonal = validation["seasonal_mean"].to_numpy()
    baseline_score = 100 * (1 - np.abs(validation["demand"].to_numpy() - baseline).sum() / validation["demand"].sum())
    weekly_score = 100 * (1 - np.abs(validation["demand"].to_numpy() - weekly).sum() / validation["demand"].sum())
    seasonal_score = 100 * (1 - np.abs(validation["demand"].to_numpy() - seasonal).sum() / validation["demand"].sum())
    blends = {}
    for alpha in np.arange(0, 1.01, 0.05):
        prediction = alpha * validation["prediction"].to_numpy() + (1 - alpha) * baseline
        blends[f"{alpha:.2f}"] = round(float(100 * (1 - np.abs(validation["demand"].to_numpy() - prediction).sum() / validation["demand"].sum())), 4)

    print(json.dumps({
        "model": best_name,
        "candidate_scores": model_scores,
        "cutoff": cutoff.isoformat(),
        "train_rows": len(train),
        "validation_rows": len(validation),
        "accuracy_wape": round(float(overall), 4),
        "official_accuracy_station_mean": round(official_accuracy, 4),
        "lag_96_accuracy_wape": round(float(baseline_score), 4),
        "lag_672_accuracy_wape": round(float(weekly_score), 4),
        "seasonal_mean_accuracy_wape": round(float(seasonal_score), 4),
        "blend_accuracy_wape": blends,
        "accuracy_by_station": {str(key): round(float(value), 4) for key, value in scores.items()},
    }, indent=2))


if __name__ == "__main__":
    main()
