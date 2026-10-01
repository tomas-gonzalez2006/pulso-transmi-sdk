"""Decide whether the official accuracy should trigger a retraining run."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx


THRESHOLD = float(os.getenv("ACCURACY_RETRAIN_THRESHOLD", "75"))
COOLDOWN_HOURS = float(os.getenv("RETRAIN_COOLDOWN_HOURS", "0"))
API_BASE = os.getenv("PULSO_API_URL", "https://pulso-transmi.72-60-245-2.sslip.io").rstrip("/")
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY")


def output(values: dict[str, str]) -> None:
    path = os.getenv("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            for key, value in values.items():
                handle.write(f"{key}={value}\n")


def official_accuracy() -> tuple[float | None, str]:
    key = os.getenv("PULSO_API_KEY")
    if not key:
        return None, "public_api_key_missing"
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    with httpx.Client(base_url=API_BASE, headers=headers, timeout=30) as client:
        identity = client.get("/v1/me")
        leaderboard = client.get("/v1/leaderboard", params={"window": "cumulative"})
        identity.raise_for_status()
        leaderboard.raise_for_status()
        name = identity.json().get("display_name")
        payload: Any = leaderboard.json()
    entries = payload.get("data", []) if isinstance(payload, dict) else payload
    row = next((item for item in entries if item.get("display_name") == name), None)
    if row and row.get("accuracy") is not None:
        accuracy = float(row["accuracy"])
        return (accuracy * 100 if accuracy <= 1 else accuracy), "public_api"
    return None, "student_not_found"


def supabase_accuracy() -> tuple[float | None, str]:
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None, "supabase_credentials_missing"
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    params = {
        "select": "accuracy,measured_at",
        "model_version": "eq.pulso-leaderboard:cumulative",
        "metric_scope": "eq.cumulative",
        "order": "measured_at.desc",
        "limit": "1",
    }
    endpoint = f"{SUPABASE_URL}/rest/v1/model_metrics"
    response = httpx.get(endpoint, headers=headers, params=params, timeout=30)
    response.raise_for_status()
    rows = response.json()
    if rows and rows[0].get("accuracy") is not None:
        return float(rows[0]["accuracy"]), "supabase:cumulative"

    # Older deployments may not have leaderboard snapshots yet. Fall back to
    # the latest released production evaluation so a real low score cannot be
    # hidden merely because the collector has not populated cumulative rows.
    production = {
        "select": "accuracy,measured_at",
        "metric_scope": "eq.production",
        "accuracy": "not.is.null",
        "order": "measured_at.desc",
        "limit": "1",
    }
    response = httpx.get(endpoint, headers=headers, params=production, timeout=30)
    response.raise_for_status()
    rows = response.json()
    return (float(rows[0]["accuracy"]), "supabase:production") if rows and rows[0].get("accuracy") is not None else (None, "metric_not_found")


def recent_training() -> bool:
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    params = {"select": "finished_at", "run_type": "eq.training", "status": "eq.succeeded", "order": "finished_at.desc", "limit": "1"}
    response = httpx.get(f"{SUPABASE_URL}/rest/v1/pipeline_runs", headers=headers, params=params, timeout=30)
    response.raise_for_status()
    rows = response.json()
    if not rows or not rows[0].get("finished_at"):
        return False
    finished = datetime.fromisoformat(str(rows[0]["finished_at"]).replace("Z", "+00:00"))
    return finished >= datetime.now(timezone.utc) - timedelta(hours=COOLDOWN_HOURS)


def main() -> None:
    try:
        accuracy, source = official_accuracy()
    except httpx.HTTPError as error:
        print(f"Official leaderboard unavailable ({error.__class__.__name__}); using Supabase.")
        accuracy, source = supabase_accuracy()
    if accuracy is None:
        try:
            accuracy, source = supabase_accuracy()
        except httpx.HTTPError:
            accuracy = None
            source = f"{source}:supabase_unavailable"
    if accuracy is None:
        output({"retrain": "false", "accuracy": "", "source": source})
        print(json.dumps({"retrain": False, "source": source, "reason": "no_accuracy"}))
        return
    should_retrain = accuracy < THRESHOLD
    cooldown = COOLDOWN_HOURS > 0 and recent_training() if should_retrain else False
    if cooldown:
        should_retrain = False
    output({"retrain": str(should_retrain).lower(), "accuracy": f"{accuracy:.6f}", "source": source})
    print(json.dumps({"retrain": should_retrain, "accuracy": accuracy, "threshold": THRESHOLD, "source": source, "cooldown": cooldown}))


if __name__ == "__main__":
    main()
