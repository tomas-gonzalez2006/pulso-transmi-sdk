"""Consume released observations and advance the cursor only after Supabase writes."""
from __future__ import annotations

import os
from datetime import datetime, timezone
import httpx

API = os.getenv("PULSO_API_URL", "https://pulso-transmi.72-60-245-2.sslip.io").rstrip("/")
URL = os.getenv("SUPABASE_URL", "").rstrip("/")
KEY = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY")
SOURCE = "pulso_stream_observations"

def main() -> None:
    if not URL or not KEY: raise SystemExit("SUPABASE_URL and SUPABASE_SERVICE_KEY are required")
    h = {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates,return=minimal"}
    with httpx.Client(timeout=60) as db:
        cursor_rows = db.get(
            f"{URL}/rest/v1/api_cursors",
            headers=h,
            params={"source_name": f"eq.{SOURCE}", "select": "cursor_value"},
        )
        cursor_rows.raise_for_status()
        cursor_rows = cursor_rows.json()
        cursor = cursor_rows[0].get("cursor_value") if cursor_rows else None
        params = {"limit": "5000"};
        if cursor: params["cursor"] = cursor
        response = httpx.get(f"{API}/v1/stream/observations", params=params, timeout=60)
        response.raise_for_status(); payload = response.json(); rows = payload.get("data", [])
        if rows:
            timestamps = sorted({row["observed_at"] for row in rows})
            slots = []
            for stamp in timestamps:
                dt = datetime.fromisoformat(stamp.replace("Z", "+00:00")); local = dt.astimezone(__import__("zoneinfo").ZoneInfo("America/Bogota"))
                slots.append({"observed_at": dt.isoformat(), "local_date": local.date().isoformat(), "local_hour": local.hour, "local_minute": local.minute, "weekday": local.isoweekday(), "is_weekend": local.weekday() >= 5})
            db.post(f"{URL}/rest/v1/time_slots", headers=h, json=slots).raise_for_status()
            demands = [{"station_id": row["station_id"], "observed_at": row["observed_at"], "demand": row["demand"]} for row in rows]
            db.post(f"{URL}/rest/v1/demand_observations", headers=h, json=demands).raise_for_status()
        next_cursor = payload.get("next_cursor")
        if next_cursor:
            db.post(f"{URL}/rest/v1/api_cursors", headers=h, json={"source_name": SOURCE, "cursor_value": next_cursor, "updated_at": datetime.now(timezone.utc).isoformat()}).raise_for_status()
        observed = [row.get("observed_at") for row in rows if row.get("observed_at")]
        latest = max(observed) if observed else "none"
        earliest = min(observed) if observed else "none"
        print(f"stream_rows={len(rows)} earliest_observed_at={earliest} latest_observed_at={latest} cursor_advanced={bool(next_cursor)}")

if __name__ == "__main__": main()
