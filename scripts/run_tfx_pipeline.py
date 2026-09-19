"""Create a versioned Pulso TransMi snapshot and run its TFX pipeline."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd


DEFAULT_API = "https://pulso-transmi.72-60-245-2.sslip.io"


def download(base: str, name: str) -> bytes:
    with urlopen(Request(f"{base.rstrip('/')}/v1/downloads/{name}", headers={"User-Agent": "pulso-transmi-tfx/1.0"}), timeout=120) as response:
        return response.read()


def build_snapshot(api: str, root: Path) -> Path:
    observations = pd.read_csv(io.BytesIO(download(api, "observations.csv")), dtype={"station_id": "string"}, parse_dates=["observed_at"])
    context = pd.read_csv(io.BytesIO(download(api, "context.csv")), parse_dates=["observed_at"])
    observations = observations.sort_values(["station_id", "observed_at"])
    group = observations.groupby("station_id", sort=False)["demand"]
    for lag in (1, 4, 96, 672):
        observations[f"lag_{lag}"] = group.shift(lag)
    local = observations["observed_at"].dt.tz_convert("America/Bogota")
    minute = local.dt.hour * 60 + local.dt.minute
    observations["minute_sin"] = np.sin(2 * np.pi * minute / 1440)
    observations["minute_cos"] = np.cos(2 * np.pi * minute / 1440)
    observations["weekday_sin"] = np.sin(2 * np.pi * local.dt.dayofweek / 7)
    observations["weekday_cos"] = np.cos(2 * np.pi * local.dt.dayofweek / 7)
    observations["station_code"] = observations["station_id"].astype("category").cat.codes.astype(float)
    frame = observations.merge(context, on="observed_at", how="left").dropna()
    frame = frame.rename(columns={"demand": "demand"})
    columns = ["station_code", "minute_sin", "minute_cos", "weekday_sin", "weekday_cos", "lag_1", "lag_4", "lag_96", "lag_672", "rain_mm", "rain_forecast", "temperature_c", "temperature_forecast", "event_intensity", "demand"]
    frame = frame[columns + ["observed_at"]].sort_values("observed_at")
    cutoff = frame["observed_at"].max() - pd.Timedelta(days=7)
    train = frame[frame["observed_at"] <= cutoff].drop(columns=["observed_at"])
    evaluation = frame[frame["observed_at"] > cutoff].drop(columns=["observed_at"])
    staging = root / "staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    train.to_csv(staging / "train.csv", index=False)
    evaluation.to_csv(staging / "eval.csv", index=False)
    digest = hashlib.sha256((staging / "train.csv").read_bytes() + (staging / "eval.csv").read_bytes()).hexdigest()[:16]
    snapshot = root / digest
    if not snapshot.exists():
        staging.rename(snapshot)
    else:
        shutil.rmtree(staging)
    (snapshot / "manifest.json").write_text(json.dumps({"version": digest, "api": api, "cutoff": cutoff.isoformat(), "train_rows": len(train), "eval_rows": len(evaluation)}, indent=2), encoding="utf-8")
    return snapshot


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default=DEFAULT_API)
    parser.add_argument("--snapshot-root", type=Path, default=Path("data/tfx/snapshots"))
    args = parser.parse_args()
    snapshot = build_snapshot(args.api_url, args.snapshot_root)
    subprocess.run([sys.executable, "pipelines/tfx_pipeline.py", "--snapshot-dir", str(snapshot)], check=True)
    print(json.dumps({"snapshot": str(snapshot), "status": "completed"}))


if __name__ == "__main__":
    main()
