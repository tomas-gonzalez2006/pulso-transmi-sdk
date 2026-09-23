"""Train, predict and submit the current Pulso TransMi forecast cycle."""

from __future__ import annotations

import io
import math
import os
import subprocess
from datetime import datetime, timezone

import httpx
import pandas as pd
from src.pulso_transmi.occupancy import future_features as xgb_future_features
from src.pulso_transmi.occupancy import train as train_xgb


BASE_URL = os.getenv("PULSO_API_URL", "https://pulso-transmi.72-60-245-2.sslip.io").rstrip("/")
API_KEY = os.getenv("PULSO_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY")


def api_get(client: httpx.Client, path: str) -> dict:
    response = client.get(path)
    if response.status_code == 404 and path == "/v1/forecast-cycles/current":
        return {}
    response.raise_for_status()
    return response.json()


def add_features(frame: pd.DataFrame) -> pd.DataFrame:
    data = frame.sort_values(["station_id", "observed_at"]).copy()
    local = data["observed_at"].dt.tz_convert("America/Bogota")
    data["hour"] = local.dt.hour
    data["day_of_week"] = local.dt.dayofweek
    data["hour_sin"] = (local.dt.hour * 60 + local.dt.minute).map(lambda value: math.sin(2 * math.pi * value / 1440))
    data["hour_cos"] = (local.dt.hour * 60 + local.dt.minute).map(lambda value: math.cos(2 * math.pi * value / 1440))
    grouped = data.groupby("station_id", observed=True)["demand"]
    data["lag_1"] = grouped.shift(1)
    data["lag_4"] = grouped.shift(4)
    data["lag_96"] = grouped.shift(96)
    data["rolling_12"] = grouped.transform(lambda values: values.shift(1).rolling(12).mean())
    data["rolling_96"] = grouped.transform(lambda values: values.shift(1).rolling(96).mean())
    return data.dropna()


def future_features(history: pd.DataFrame, targets: list[dict]) -> pd.DataFrame:
    rows = []
    for target in targets:
        station = target["station_id"]
        timestamp = pd.Timestamp(target["target_at"])
        local = timestamp.tz_convert("America/Bogota")
        values = history.loc[history["station_id"] == station].sort_values("observed_at")["demand"].to_numpy()
        if len(values) < 96:
            raise RuntimeError(f"Not enough history for station {station}")
        rows.append({
            "station_id": station,
            "target_at": target["target_at"],
            "hour": local.hour,
            "day_of_week": local.dayofweek,
            "hour_sin": math.sin(2 * math.pi * (local.hour * 60 + local.minute) / 1440),
            "hour_cos": math.cos(2 * math.pi * (local.hour * 60 + local.minute) / 1440),
            "lag_1": values[-1],
            "lag_4": values[-4],
            "lag_96": values[-96],
            "rolling_12": values[-12:].mean(),
            "rolling_96": values[-96:].mean(),
        })
    return pd.DataFrame(rows)


def git_commit() -> str | None:
    try:
        value = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
        return value if len(value) == 40 else None
    except (OSError, subprocess.CalledProcessError):
        return None


def record_observability(run_id: str, cycle: dict, predictions: list[dict], receipt: dict, model_trace: dict) -> None:
    """Persist the accepted batch for the dashboard; never raises on telemetry failure."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        print("Observability skipped: Supabase service credentials are not configured.")
        return
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    try:
        with httpx.Client(timeout=30) as db:
            db.post(f"{SUPABASE_URL}/rest/v1/pipeline_runs", headers=headers, json={
                "run_id": run_id, "run_type": "inference", "status": "succeeded",
                "finished_at": datetime.now(timezone.utc).isoformat(), "cycle_id": cycle["cycle_id"],
                "model_version": model_trace["version"], "git_commit": model_trace.get("git_commit"),
                "records_processed": len(predictions), "metadata": {"submission_id": receipt.get("submission_id")},
            }).raise_for_status()
            rows = []
            for target, prediction in zip(cycle["targets"], predictions, strict=True):
                rows.append({
                    "cycle_id": cycle["cycle_id"], "submission_id": receipt.get("submission_id"),
                    "station_id": prediction["station_id"], "target_at": prediction["target_at"],
                    "horizon_minutes": target.get("horizon_minutes"), "predicted_value": prediction["value"],
                    "model_version": model_trace["version"],
                })
            db.post(f"{SUPABASE_URL}/rest/v1/prediction_records", headers=headers, json=rows).raise_for_status()
    except httpx.HTTPError as error:
        print(f"Observability warning: {error.__class__.__name__}")


def main() -> None:
    if not API_KEY:
        raise SystemExit("PULSO_API_KEY is required")

    headers = {"Authorization": f"Bearer {API_KEY}", "User-Agent": "pulso-transmi-student-pipeline/1.0"}
    with httpx.Client(base_url=BASE_URL, headers=headers, timeout=60, follow_redirects=True) as client:
        identity = api_get(client, "/v1/me")
        cycle = api_get(client, "/v1/forecast-cycles/current")
        if not cycle:
            print("No open forecast cycle; nothing to submit.")
            return

        response = client.get("/v1/downloads/observations.csv")
        response.raise_for_status()
        history = pd.read_csv(io.BytesIO(response.content), dtype={"station_id": "string"})
        history["observed_at"] = pd.to_datetime(history["observed_at"], utc=True)
        cutoff = pd.Timestamp(cycle["data_cutoff"])
        history = history[history["observed_at"] <= cutoff].copy()

        context_response = client.get("/v1/downloads/context.csv")
        context_response.raise_for_status()
        context = pd.read_csv(io.BytesIO(context_response.content))
        context["observed_at"] = pd.to_datetime(context["observed_at"], utc=True)
        context = context[context["observed_at"] <= cutoff].copy()
        trained = train_xgb(history, context)
        future = xgb_future_features(history, context, cycle["targets"], trained, cutoff)
        predictions_array = trained.model.predict(future)
        predictions = [{
            "station_id": row.station_id,
            "target_at": row.target_at,
            "value": max(0.0, round(float(value), 3)),
        } for row, value in zip(pd.DataFrame(cycle["targets"]).itertuples(index=False), predictions_array, strict=True)]

        run_id = f"gha-{os.getenv('GITHUB_RUN_ID', 'local')}-{os.getenv('GITHUB_RUN_ATTEMPT', '1')}-{cycle['cycle_id']}"
        model_commit = git_commit()
        model_trace = {
            "version": "xgboost-occupancy:1.0",
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "training_data_end": cycle["data_cutoff"],
        }
        if model_commit:
            model_trace["git_commit"] = model_commit
        payload = {
            "schema_version": "1.0",
            "cycle_id": cycle["cycle_id"],
            "client_run_id": run_id,
            "data_cutoff": cycle["data_cutoff"],
            "model": model_trace,
            "predictions": predictions,
        }
        idempotency_key = f"pulso-{cycle['cycle_id']}-xgboost-occupancy-1.0"
        submission = client.post("/v1/submissions", headers={"Idempotency-Key": idempotency_key}, json=payload)
        if submission.status_code == 409:
            print(f"Submission already exists for cycle {cycle['cycle_id']}; skipping duplicate.")
            # Pulso has already accepted this idempotency key. Persist the
            # locally generated batch as well so the dashboard can display it.
            record_observability(
                run_id,
                cycle,
                predictions,
                {"status": "already_exists"},
                model_trace,
            )
            return
        submission.raise_for_status()
        receipt = submission.json()
        record_observability(run_id, cycle, predictions, receipt, model_trace)
        print(f"Student: {identity.get('display_name', 'unknown')}")
        print(f"Submission: {receipt['submission_id']}")
        print(f"Status: {receipt['status']}")
        print(f"Predictions: {receipt['predictions_received']}/{receipt['expected_predictions']}")


if __name__ == "__main__":
    main()
