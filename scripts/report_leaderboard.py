"""Print the authenticated student's current leaderboard metrics."""
from __future__ import annotations
import os
import httpx

base = os.getenv("PULSO_API_URL", "https://pulso-transmi.72-60-245-2.sslip.io").rstrip("/")
key = os.environ["PULSO_API_KEY"]
headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
with httpx.Client(timeout=30, headers=headers) as client:
    identity = client.get(f"{base}/v1/me"); identity.raise_for_status(); identity = identity.json()
    response = client.get(f"{base}/v1/leaderboard", params={"window": "cumulative"})
    response.raise_for_status(); payload = response.json()
name = identity.get("display_name")
entries = payload.get("data", payload if isinstance(payload, list) else [])
mine = next((row for row in entries if row.get("display_name") == name), None)
print({"student": name, "leaderboard": mine})
