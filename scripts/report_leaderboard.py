"""Collect the authenticated student's cumulative leaderboard metrics."""
from __future__ import annotations
import json
import os
from typing import Any
import httpx

LEADERBOARD_MODEL = "pulso-leaderboard:cumulative"


def _fraction(value: Any) -> float | None:
    if value is None:
        return None
    number = float(value)
    return number / 100 if number > 1 else number


def build_metric(student: str, row: dict[str, Any]) -> dict[str, Any]:
    """Convert one leaderboard row to the ``model_metrics`` schema."""
    accuracy = float(row["accuracy"])
    if accuracy <= 1:
        accuracy *= 100
    wape = row.get("wape")
    if wape is None:
        # The leaderboard exposes accuracy; accuracy = (1 - WAPE) * 100.
        wape = max(0.0, 1.0 - accuracy / 100.0)
    else:
        wape = _fraction(wape)
    rank = row.get("rank")
    return {
        "model_version": LEADERBOARD_MODEL,
        "metric_scope": "cumulative",
        "accuracy": accuracy,
        "wape": wape,
        "coverage": _fraction(row.get("coverage")),
        "reference_window": "leaderboard:cumulative",
        "metadata": {
            "student": student,
            "leaderboard_window": "cumulative",
            "rank": int(rank) if rank is not None else None,
            "source_row": row,
        },
    }


def _leaderboard_row(payload: Any, name: str) -> dict[str, Any] | None:
    entries = payload.get("data", []) if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        return None
    return next(
        (row for row in entries if isinstance(row, dict) and row.get("display_name") == name),
        None,
    )


def _changed(previous: dict[str, Any] | None, metric: dict[str, Any]) -> bool:
    if not previous:
        return True
    for key in ("accuracy", "wape", "coverage"):
        old, new = previous.get(key), metric.get(key)
        if old is None or new is None:
            if old != new:
                return True
        elif abs(float(old) - float(new)) > 1e-9:
            return True
    return (previous.get("metadata") or {}).get("rank") != (
        metric.get("metadata") or {}
    ).get("rank")


def main() -> None:
    api_key = os.environ["PULSO_API_KEY"]
    api_base = os.getenv(
        "PULSO_API_URL", "https://pulso-transmi.72-60-245-2.sslip.io"
    ).rstrip("/")
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    with httpx.Client(timeout=30, headers=headers) as client:
        identity_response = client.get(f"{api_base}/v1/me")
        identity_response.raise_for_status()
        name = identity_response.json().get("display_name")
        if not name:
            raise RuntimeError("The public API did not return display_name")
        leaderboard_response = client.get(
            f"{api_base}/v1/leaderboard", params={"window": "cumulative"}
        )
        leaderboard_response.raise_for_status()
        row = _leaderboard_row(leaderboard_response.json(), name)
        if row is None:
            print(json.dumps({"student": name, "status": "not_in_leaderboard"}))
            return
        metric = build_metric(name, row)

    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY")
    if not supabase_url or not supabase_key:
        print(json.dumps({"student": name, "metric": metric, "status": "not_persisted"}))
        return
    db_headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    db_base = supabase_url.rstrip("/") + "/rest/v1/model_metrics"
    with httpx.Client(timeout=30, headers=db_headers) as db:
        latest_response = db.get(
            db_base,
            params={
                "select": "accuracy,wape,coverage,metadata,drift_score,measured_at",
                "model_version": f"eq.{LEADERBOARD_MODEL}",
                "metric_scope": "eq.cumulative",
                "order": "measured_at.desc",
                "limit": "1",
            },
        )
        latest_response.raise_for_status()
        latest = latest_response.json()
        previous = latest[0] if latest else None
        if not _changed(previous, metric):
            print(json.dumps({"student": name, "status": "unchanged", "metric": metric}))
            return
        metric["drift_score"] = (
            metric["accuracy"] - float(previous["accuracy"])
            if previous and previous.get("accuracy") is not None
            else None
        )
        insert_response = db.post(
            db_base,
            headers={**db_headers, "Prefer": "return=minimal"},
            json=metric,
        )
        insert_response.raise_for_status()
    print(json.dumps({"student": name, "status": "inserted", "metric": metric}))


if __name__ == "__main__":
    main()
