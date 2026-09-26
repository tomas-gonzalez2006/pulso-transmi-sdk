"""Collect cumulative leaderboard snapshots for every student."""
from __future__ import annotations

import json
import os
from typing import Any

import httpx
from pulso_transmi.leaderboard import build_metric

LEADERBOARD_MODEL = "pulso-leaderboard:cumulative"


def _leaderboard_entries(payload: Any) -> list[dict[str, Any]]:
    entries = payload.get("data", []) if isinstance(payload, dict) else payload
    return [entry for entry in entries if isinstance(entry, dict)] if isinstance(entries, list) else []


def _student(metric: dict[str, Any]) -> str | None:
    return (metric.get("metadata") or {}).get("student")


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
    api_base = os.getenv("PULSO_API_URL", "https://pulso-transmi.72-60-245-2.sslip.io").rstrip("/")
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    with httpx.Client(timeout=30, headers=headers) as client:
        identity_response = client.get(f"{api_base}/v1/me")
        identity_response.raise_for_status()
        identity = identity_response.json()
        name = identity.get("display_name")
        if not name:
            raise RuntimeError("The public API did not return display_name")
        leaderboard_response = client.get(f"{api_base}/v1/leaderboard", params={"window": "cumulative"})
        leaderboard_response.raise_for_status()
        entries = _leaderboard_entries(leaderboard_response.json())

    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY")
    if not supabase_url or not supabase_key:
        print(json.dumps({"student": name, "students": len(entries), "status": "not_persisted"}))
        return

    db_headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    db_base = supabase_url.rstrip("/") + "/rest/v1/model_metrics"
    with httpx.Client(timeout=30, headers=db_headers) as db:
        response = db.get(
            db_base,
            params={
                "select": "accuracy,wape,coverage,metadata,drift_score,measured_at",
                "model_version": f"eq.{LEADERBOARD_MODEL}",
                "metric_scope": "eq.cumulative",
                "order": "measured_at.desc",
                "limit": "5000",
            },
        )
        response.raise_for_status()
        latest_by_student: dict[str, dict[str, Any]] = {}
        for previous in response.json():
            student = _student(previous)
            if student and student not in latest_by_student:
                latest_by_student[student] = previous

        inserted = 0
        for row in entries:
            student = str(row.get("display_name", "")).strip()
            if not student or "accuracy" not in row:
                continue
            metric = build_metric(student, row)
            previous = latest_by_student.get(student)
            if not _changed(previous, metric):
                continue
            metric["drift_score"] = (
                metric["accuracy"] - float(previous["accuracy"])
                if previous and previous.get("accuracy") is not None
                else None
            )
            insert = db.post(db_base, headers={**db_headers, "Prefer": "return=minimal"}, json=metric)
            insert.raise_for_status()
            inserted += 1

    print(json.dumps({"student": name, "students": len(entries), "inserted": inserted, "status": "ok"}))


if __name__ == "__main__":
    main()
