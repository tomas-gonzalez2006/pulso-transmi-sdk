"""Train production-faithful candidates and promote the winner in Supabase."""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
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


def load_data() -> tuple[pd.DataFrame, pd.DataFrame, bytes, bytes, bytes, bytes]:
    with httpx.Client(timeout=120) as client:
        observations_bytes = client.get(f"{API}/v1/downloads/observations.csv").content
        context_bytes = client.get(f"{API}/v1/downloads/context.csv").content
        stations_bytes = client.get(f"{API}/v1/downloads/stations.csv").content
        metadata_bytes = client.get(f"{API}/v1/downloads/metadata.json").content
    observations = pd.read_csv(io.BytesIO(observations_bytes), dtype={"station_id": "string"})
    context = pd.read_csv(io.BytesIO(context_bytes))
    observations["observed_at"] = pd.to_datetime(observations["observed_at"], utc=True)
    context["observed_at"] = pd.to_datetime(context["observed_at"], utc=True)
    return observations, context, observations_bytes, context_bytes, stations_bytes, metadata_bytes


def evaluate(observations: pd.DataFrame, context: pd.DataFrame, parameters: dict[str, object], target_transform: str) -> tuple[float, float, int]:
    cutoff = observations["observed_at"].max() - pd.Timedelta(days=7)
    history = observations[observations["observed_at"] <= cutoff].copy()
    validation = observations[observations["observed_at"] > cutoff].copy()
    context_train = context[context["observed_at"] <= cutoff].copy()
    if history.empty or validation.empty:
        raise RuntimeError("Not enough data for temporal validation")
    trained = train(history, context_train, parameters, target_transform)
    targets = [{"station_id": str(row.station_id), "target_at": row.observed_at.isoformat()} for row in validation.itertuples()]
    predicted = np.asarray(recursive_predict(history, context_train, targets, trained, cutoff), dtype=float)
    actual = validation["demand"].to_numpy(dtype=float)
    denominator = float(np.abs(actual).sum())
    wape = float(np.abs(actual - predicted).sum()) / denominator if denominator else 0.0
    return max(0.0, 100 * (1 - wape)), wape, len(validation)


def main() -> None:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise SystemExit("SUPABASE_URL and SUPABASE_SERVICE_KEY are required for version registration")

    observations, context, observations_bytes, context_bytes, stations_bytes, metadata_bytes = load_data()
    data_version = f"data-{sha256(observations_bytes)[:12]}-{sha256(context_bytes)[:12]}"
    data_cutoff = observations["observed_at"].max()
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
        current_accuracy = ((current or {}).get("metrics") or {}).get("validation_accuracy")
        promoted = current_accuracy is None or winner_accuracy > float(current_accuracy)

        trained = train(observations, context, winner_parameters, winner_transform)
        trained.parameters = {**(trained.parameters or {}), "candidate": winner_name}
        artifact = REPO_ROOT / "artifacts" / "models" / f"{model_version}.joblib"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(trained, artifact)
        artifact_bytes = artifact.read_bytes()
        artifact_hash = sha256(artifact_bytes)
        commit = git_commit()
        validation_metrics = {"feature_count": len(FEATURES), "validation_accuracy": winner_accuracy, "validation_wape": winner_wape, "validation_rows": int(winner["validation_rows"]), "validation_window": "last_7_days_recursive", "data_cutoff": data_cutoff.isoformat(), "target_transform": winner_transform, "parameters": winner_parameters, "candidate_results": candidates}
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
