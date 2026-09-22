"""Set GitHub output retrain=true when demand drift exceeds DRIFT_THRESHOLD."""
from __future__ import annotations
import os
import httpx

URL = os.getenv("SUPABASE_URL", "").rstrip("/"); KEY = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY")
THRESHOLD = float(os.getenv("DRIFT_THRESHOLD", "0.20"))

def main() -> None:
    if not URL or not KEY: raise SystemExit("SUPABASE_URL and SUPABASE_SERVICE_KEY are required")
    h = {"apikey": KEY, "Authorization": f"Bearer {KEY}"}
    params = {"select":"demand,observed_at", "order":"observed_at.desc", "limit":"50000"}
    response = httpx.get(f"{URL}/rest/v1/demand_observations", headers=h, params=params, timeout=60)
    response.raise_for_status()
    rows = response.json()
    if not rows:
        raise SystemExit("No observations available to calculate drift")
    from datetime import datetime, timedelta
    latest = datetime.fromisoformat(str(rows[0]["observed_at"]).replace("Z", "+00:00"))
    recent_cut = (latest - timedelta(days=7)).isoformat()
    base_cut = (latest - timedelta(days=14)).isoformat()
    recent = [float(r["demand"]) for r in rows if str(r["observed_at"]) >= recent_cut]; baseline = [float(r["demand"]) for r in rows if str(r["observed_at"]) < recent_cut]
    score = abs(sum(recent)/len(recent) - sum(baseline)/len(baseline)) / (abs(sum(baseline)/len(baseline)) or 1) if recent and baseline else 0.0
    retrain = score >= THRESHOLD
    output = os.getenv("GITHUB_OUTPUT")
    if output: open(output, "a", encoding="utf-8").write(f"retrain={'true' if retrain else 'false'}\ndrift={score:.6f}\n")
    print(f"drift={score:.4%} threshold={THRESHOLD:.0%} retrain={retrain}")

if __name__ == "__main__": main()
