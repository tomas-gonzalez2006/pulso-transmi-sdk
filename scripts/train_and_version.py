"""Train the promoted XGBoost model and register immutable data/model versions."""

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

from src.pulso_transmi.occupancy import FEATURES, recursive_predict, train

API = os.getenv("PULSO_API_URL", "https://pulso-transmi.72-60-245-2.sslip.io").rstrip("/")
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY")


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def git_commit() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def validation_accuracy(observations: pd.DataFrame, context: pd.DataFrame) -> tuple[float, int]:
    """Evaluate a candidate on the final seven days held out from training."""
    cutoff = observations["observed_at"].max() - pd.Timedelta(days=7)
    history = observations[observations["observed_at"] <= cutoff].copy()
    validation = observations[observations["observed_at"] > cutoff].copy()
    if history.empty or validation.empty:
        raise RuntimeError("Not enough data for temporal validation")
    context_train = context[context["observed_at"] <= cutoff].copy()
    trained = train(history, context_train)
    targets = [
        {"station_id": str(row.station_id), "target_at": row.observed_at.isoformat()}
        for row in validation.itertuples()
    ]
    predicted = np.asarray(recursive_predict(history, context_train, targets, trained, cutoff), dtype=float)
    actual = validation["demand"].to_numpy(dtype=float)
    denominator = float(abs(actual).sum())
    score = 100 * (1 - float(abs(actual - predicted).sum()) / denominator) if denominator else 0.0
    return max(0.0, score), len(validation)


def main() -> None:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise SystemExit("SUPABASE_URL and SUPABASE_SERVICE_KEY are required for version registration")
    with httpx.Client(timeout=120) as client:
        observations_bytes = client.get(f"{API}/v1/downloads/observations.csv").content
        context_bytes = client.get(f"{API}/v1/downloads/context.csv").content
        stations_bytes = client.get(f"{API}/v1/downloads/stations.csv").content
        metadata_bytes = client.get(f"{API}/v1/downloads/metadata.json").content
    observations = pd.read_csv(io.BytesIO(observations_bytes), dtype={"station_id": "string"})
    context = pd.read_csv(io.BytesIO(context_bytes))
    observations["observed_at"] = pd.to_datetime(observations["observed_at"], utc=True)
    context["observed_at"] = pd.to_datetime(context["observed_at"], utc=True)
    trained = train(observations, context)
    validation_score, validation_rows = validation_accuracy(observations, context)
    data_version = f"data-{sha256(observations_bytes)[:12]}-{sha256(context_bytes)[:12]}"
    model_version = f"xgboost-occupancy-recursive-{sha256((data_version + json.dumps(FEATURES)).encode())[:12]}"
    artifact = Path("artifacts/models") / f"{model_version}.joblib"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(trained.model, artifact)
    artifact_bytes = artifact.read_bytes(); artifact_hash = sha256(artifact_bytes); commit = git_commit()
    model_path = f"{model_version}.joblib"
    db_headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates,return=minimal"}
    dataset_payload = {"dataset_version": data_version, "api_url": API, "observations_sha256": sha256(observations_bytes), "context_sha256": sha256(context_bytes), "stations_sha256": sha256(stations_bytes), "metadata_sha256": sha256(metadata_bytes), "cutoff_at": observations["observed_at"].max().isoformat(), "rows_observations": len(observations), "rows_context": len(context), "metadata": json.loads(metadata_bytes)}
    validation_metrics = {"feature_count": len(FEATURES), "validation_accuracy": round(validation_score, 6), "validation_rows": validation_rows, "validation_window": "last_7_days"}
    with httpx.Client(timeout=60) as client:
        champions = client.get(f"{SUPABASE_URL}/rest/v1/model_versions", headers=db_headers, params={"select": "model_version,metrics", "status": "eq.champion", "order": "created_at.desc", "limit": "1"})
        champions.raise_for_status()
        current = champions.json()[0] if champions.json() else None
        current_score = ((current or {}).get("metrics") or {}).get("validation_accuracy")
        promote = current_score is None or validation_score > float(current_score)
        model_payload = {"model_version": model_version, "dataset_version": data_version, "algorithm": "xgboost", "artifact_sha256": artifact_hash, "artifact_path": model_path, "git_commit": commit, "status": "candidate", "metrics": validation_metrics, "feature_schema": FEATURES}
        client.post(f"{SUPABASE_URL}/rest/v1/dataset_versions", headers=db_headers, json=dataset_payload).raise_for_status()
        client.post(f"{SUPABASE_URL}/rest/v1/model_versions", headers=db_headers, json=model_payload).raise_for_status()
        if promote:
            if current:
                client.patch(f"{SUPABASE_URL}/rest/v1/model_versions", headers=db_headers, params={"model_version": f"eq.{current['model_version']}"}, json={"status": "retired"}).raise_for_status()
            client.patch(f"{SUPABASE_URL}/rest/v1/model_versions", headers=db_headers, params={"model_version": f"eq.{model_version}"}, json={"status": "champion"}).raise_for_status()
        client.post(f"{SUPABASE_URL}/rest/v1/pipeline_runs", headers=db_headers, json={"run_id": f"training-{model_version}", "run_type": "training", "status": "succeeded", "finished_at": datetime.now(timezone.utc).isoformat(), "model_version": model_version, "git_commit": commit, "records_processed": len(observations), "metadata": {"dataset_version": data_version, "artifact_sha256": artifact_hash}}).raise_for_status()
        upload = client.post(f"{SUPABASE_URL}/storage/v1/object/model-artifacts/{model_path}", headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/octet-stream", "x-upsert": "true"}, content=artifact_bytes)
        upload.raise_for_status()
    print(json.dumps({"dataset_version": data_version, "model_version": model_version, "artifact_sha256": artifact_hash, "rows": len(observations), "feature_count": len(FEATURES), "validation_accuracy": validation_score, "current_champion_accuracy": current_score, "promoted": promote}))


if __name__ == "__main__":
    main()
