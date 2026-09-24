"""Reconcile the official API downloads into Supabase using idempotent upserts."""
from __future__ import annotations

import csv
import io
import os
from datetime import datetime

import httpx

API = os.getenv("PULSO_API_URL", "https://pulso-transmi.72-60-245-2.sslip.io").rstrip("/")
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY", "")


def csv_download(client: httpx.Client, name: str) -> list[dict[str, str]]:
    response = client.get(f"{API}/v1/downloads/{name}.csv")
    response.raise_for_status()
    return list(csv.DictReader(io.StringIO(response.text.lstrip("\ufeff"))))


def chunks(rows: list[dict], size: int = 500):
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


def main() -> None:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise SystemExit("SUPABASE_URL and SUPABASE_SERVICE_KEY are required")
    db_headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    with httpx.Client(timeout=120, headers={"User-Agent": "pulso-transmi-reconciler/1.0"}) as api:
        observations = csv_download(api, "observations")
        context = csv_download(api, "context")
    with httpx.Client(timeout=120) as db:
        for group in chunks(observations):
            rows = [{"station_id": r["station_id"], "observed_at": r["observed_at"], "demand": float(r["demand"])} for r in group]
            response = db.post(f"{SUPABASE_URL}/rest/v1/demand_observations", headers=db_headers, json=rows)
            response.raise_for_status()
        for group in chunks(context):
            weather = [{
                "observed_at": r["observed_at"], "rain_mm": float(r["rain_mm"] or 0),
                "rain_forecast": float(r["rain_forecast"]) if r.get("rain_forecast") else None,
                "temperature_c": float(r["temperature_c"]) if r.get("temperature_c") else None,
                "temperature_forecast": float(r["temperature_forecast"]) if r.get("temperature_forecast") else None,
            } for r in group]
            response = db.post(f"{SUPABASE_URL}/rest/v1/weather_observations", headers=db_headers, json=weather)
            response.raise_for_status()
    latest = max((r["observed_at"] for r in observations), default=None)
    print(f"official_observations={len(observations)} official_context={len(context)} latest_observed_at={latest}")


if __name__ == "__main__":
    main()
