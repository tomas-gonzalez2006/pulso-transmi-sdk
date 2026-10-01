"""Train production-faithful candidates and promote the winner in Supabase."""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import joblib
import numpy as np
import pandas as pd

from pulso_transmi.occupancy import FEATURES, recursive_predict, train

REPO_ROOT = Path(__file__).resolve().parents[1]
API = os.getenv("PULSO_API_URL", "https://pulso-transmi.72-60-245-2.sslip.io").rstrip("/")
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY")

CANDIDATES = (
    ("xgboost-log1p", {}, "log1p"),
    ("xgboost-identity", {"n_jobs": 4}, "identity"),
)


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def git_commit() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def load_data(cutoff: pd.Timestamp | None = None) -> tuple[pd.DataFrame, pd.DataFrame, bytes, bytes, bytes, bytes]:
    def download(client: httpx.Client, path: str) -> bytes:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = client.get(path)
                response.raise_for_status()
                return response.content
            except (httpx.HTTPError, httpx.TimeoutException) as error:
                last_error = error
                if attempt < 2:
                    time.sleep(2 ** attempt)
        raise RuntimeError(f"Could not download {path} after 3 attempts") from last_error

    with httpx.Client(timeout=httpx.Timeout(180, connect=45)) as client:
        context_bytes = download(client, f"{API}/v1/downloads/context.csv")
        stations_bytes = download(client, f"{API}/v1/downloads/stations.csv")
        metadata_bytes = download(client, f"{API}/v1/downloads/metadata.json")
    context = pd.read_csv(io.BytesIO(context_bytes))
    context["observed_at"] = pd.to_datetime(context["observed_at"], utc=True)
    if SUPABASE_URL and SUPABASE_KEY:
        headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
        params = {
            "select": "station_id,observed_at,demand",
            "order": "observed_at.asc",
            "limit": "100000",
        }
        if cutoff is not None:
            params["observed_at"] = f"lte.{cutoff.isoformat()}"
        response = httpx.get(
            f"{SUPABASE_URL}/rest/v1/demand_observations",
            headers=headers,
            params=params,
            timeout=120,
        )
        response.raise_for_status()
        observations = pd.DataFrame(response.json())
        if observations.empty:
            raise RuntimeError("Supabase returned no observations for the requested training cutoff")
        observations["station_id"] = observations["station_id"].astype("string")
        observations["observed_at"] = pd.to_datetime(observations["observed_at"], utc=True)
        observations["demand"] = pd.to_numeric(observations["demand"], errors="raise")
        observations_bytes = observations.to_csv(index=False).encode("utf-8")
    else:
        with httpx.Client(timeout=120) as client:
            observations_bytes = download(client, f"{API}/v1/downloads/observations.csv")
        observations = pd.read_csv(io.BytesIO(observations_bytes), dtype={"station_id": "string"})
        observations["observed_at"] = pd.to_datetime(observations["observed_at"], utc=True)
        if cutoff is not None:
            observations = observations[observations["observed_at"] <= cutoff].copy()
            observations_bytes = observations.to_csv(index=False).encode("utf-8")
    return observations, context, observations_bytes, context_bytes, stations_bytes, metadata_bytes


def evaluate(observations: pd.DataFrame, context: pd.DataFrame, parameters: dict[str, object], target_transform: str) -> tuple[float, float, int]:
    cutoff = observations["observed_at"].max() - pd.Timedelta(7, unit="D")
    history = observations[observations["observed_at"] <= cutoff].copy()
    validation = observations[observations["observed_at"] > cutoff].copy()
    context_train = context[context["observed_at"] <= cutoff].copy()
    if history.empty or validation.empty:
        raise RuntimeError("Not enough data for temporal validation")
    trained = train(history, context_train, parameters, target_transform)
    targets = [{"station_id": str(row.station_id), "target_at": row.observed_at.isoformat()} for row in validation.itertuples()]
    predicted = np.asarray(recursive_predict(history, context_train, targets, trained, cutoff), dtype=float)
    actual = validation["demand"].to_numpy(dtype=float)
    # Match the official leaderboard: average station-level WAPE.
    errors = pd.Series(np.abs(actual - predicted), index=validation.index)
    actual_series = pd.Series(actual, index=validation.index)
    station = validation["station_id"].astype(str)
    station_wape = errors.groupby(station).sum() / actual_series.groupby(station).sum()
    wape = float(station_wape.mean()) if not station_wape.empty else 0.0
    return max(0.0, 100 * (1 - wape)), wape, len(validation)


def main() -> None:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise SystemExit("SUPABASE_URL and SUPABASE_SERVICE_KEY are required for version registration")

    cutoff_env = os.getenv("TRAINING_CUTOFF")
    requested_cutoff = pd.Timestamp(cutoff_env) if cutoff_env else None
    if requested_cutoff is not None:
        requested_cutoff = requested_cutoff.tz_localize("UTC") if requested_cutoff.tzinfo is None else requested_cutoff.tz_convert("UTC")
    observations, context, observations_bytes, context_bytes, stations_bytes, metadata_bytes = load_data(requested_cutoff)
    data_cutoff = observations["observed_at"].max()
    if requested_cutoff is not None and data_cutoff > requested_cutoff:
        raise RuntimeError("Training data exceeds the requested cutoff")
    # The public context download can lag the released observation stream.
    # Carry the last known context forward so post-release observations are
    # not silently discarded from training.
    context = context.sort_values("observed_at").drop_duplicates("observed_at")
    context_columns = ["rain_mm", "rain_forecast", "temperature_c", "temperature_forecast", "event_intensity"]
    context_index = pd.DatetimeIndex(sorted(set(context["observed_at"]) | set(observations["observed_at"])))
    context = context.set_index("observed_at").reindex(context_index).ffill().reset_index(names="observed_at")
    if context[context_columns].isna().any().any():
        raise RuntimeError("Context has no usable values at the requested training cutoff")
    data_version = f"data-{sha256(observations_bytes)[:12]}-{sha256(context_bytes)[:12]}"
    candidates: list[dict[str, object]] = []
    for name, parameters, target_transform in CANDIDATES:
        accuracy, wape, rows = evaluate(observations, context, parameters, target_transform)
        candidates.append({"name": name, "accuracy": round(accuracy, 6), "wape": round(wape, 6), "validation_rows": rows, "parameters": parameters, "target_transform": target_transform})

    winner = max(candidates, key=lambda item: float(item["accuracy"]))
    winner_name = str(winner["name"])
    winner_parameters = dict(winner["parameters"])
    winner_transform = str(winner["target_transform"])
    winner_accuracy = float(winner["accuracy"])
    winner_wape = float(winner["wape"])
    model_version = f"xgboost-occupancy-recursive-{winner_transform}-{sha256((data_version + winner_name + json.dumps(winner_parameters, sort_keys=True)).encode())[:12]}"

    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates,return=minimal"}
    with httpx.Client(timeout=120) as client:
        champions = client.get(f"{SUPABASE_URL}/rest/v1/model_versions", headers=headers, params={"select": "model_version,metrics", "status": "eq.champion", "order": "created_at.desc", "limit": "1"})
        champions.raise_for_status()
        current = champions.json()[0] if champions.json() else None
        current_metrics = (current or {}).get("metrics") or {}
        current_accuracy = current_metrics.get("validation_accuracy")
        current_aggregation = current_metrics.get("metric_aggregation")
        # Older champions used network-level WAPE, which is not comparable to
        # the station-mean metric used now. Re-evaluate the champion before
        # blocking a candidate; until then, do not compare unlike metrics.
        promoted = (
            current_accuracy is None
            or current_aggregation != "station_mean_wape"
            or winner_accuracy > float(current_accuracy)
        )

        trained = train(observations, context, winner_parameters, winner_transform)
        trained.parameters = {**(trained.parameters or {}), "candidate": winner_name}
        artifact = REPO_ROOT / "artifacts" / "models" / f"{model_version}.joblib"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(trained, artifact)
        artifact_bytes = artifact.read_bytes()
        artifact_hash = sha256(artifact_bytes)
        commit = git_commit()
        validation_metrics = {"feature_count": len(FEATURES), "validation_accuracy": winner_accuracy, "validation_wape": winner_wape, "metric_aggregation": "station_mean_wape", "validation_rows": int(winner["validation_rows"]), "validation_window": "last_7_days_recursive", "data_cutoff": data_cutoff.isoformat(), "target_transform": winner_transform, "parameters": winner_parameters, "candidate_results": candidates}
        dataset_payload = {"dataset_version": data_version, "api_url": API, "observations_sha256": sha256(observations_bytes), "context_sha256": sha256(context_bytes), "stations_sha256": sha256(stations_bytes), "metadata_sha256": sha256(metadata_bytes), "cutoff_at": data_cutoff.isoformat(), "rows_observations": len(observations), "rows_context": len(context), "metadata": json.loads(metadata_bytes)}
        model_payload = {"model_version": model_version, "dataset_version": data_version, "algorithm": "xgboost", "artifact_sha256": artifact_hash, "artifact_path": f"models/{model_version}.joblib", "git_commit": commit, "status": "candidate", "metrics": validation_metrics, "feature_schema": list(FEATURES)}
        client.post(f"{SUPABASE_URL}/rest/v1/dataset_versions", headers=headers, json=dataset_payload).raise_for_status()
        client.post(f"{SUPABASE_URL}/rest/v1/model_versions", headers=headers, json=model_payload).raise_for_status()
        upload = client.post(f"{SUPABASE_URL}/storage/v1/object/model-artifacts/models/{model_version}.joblib", headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/octet-stream", "x-upsert": "true"}, content=artifact_bytes)
        upload.raise_for_status()
        if promoted:
            if current:
                client.patch(f"{SUPABASE_URL}/rest/v1/model_versions", headers=headers, params={"model_version": f"eq.{current['model_version']}"}, json={"status": "retired"}).raise_for_status()
            client.patch(f"{SUPABASE_URL}/rest/v1/model_versions", headers=headers, params={"model_version": f"eq.{model_version}"}, json={"status": "champion"}).raise_for_status()
        client.post(f"{SUPABASE_URL}/rest/v1/pipeline_runs", headers=headers, json={"run_id": f"training-{model_version}", "run_type": "training", "status": "succeeded", "finished_at": datetime.now(timezone.utc).isoformat(), "model_version": model_version, "git_commit": commit, "records_processed": len(observations), "metadata": {"dataset_version": data_version, "artifact_sha256": artifact_hash, "promoted": promoted}}).raise_for_status()

    try:
        import mlflow

        tracking_uri = os.getenv("MLFLOW_TRACKING_URI", f"sqlite:///{REPO_ROOT / 'mlflow.db'}")
        mlflow.set_tracking_uri(tracking_uri)
        mlflow.set_experiment("pulso-transmi-occupancy")
        with mlflow.start_run(run_name=model_version):
            mlflow.log_params({"model_version": model_version, "dataset_version": data_version, "algorithm": "xgboost", "target_transform": winner_transform, "promoted": str(promoted).lower()})
            mlflow.log_metrics({"validation_wape": winner_wape, "validation_accuracy": winner_accuracy, "train_rows": len(observations), "validation_rows": int(winner["validation_rows"])})
            mlflow.log_artifact(str(artifact), artifact_path="model")
    except Exception as error:
        print(f"[MLflow] Warning: logging failed or skipped: {error.__class__.__name__}")

    print(json.dumps({"dataset_version": data_version, "model_version": model_version, "winner": winner, "current_champion_accuracy": current_accuracy, "promoted": promoted, "artifact_sha256": artifact_hash}))


if __name__ == "__main__":
    main()
