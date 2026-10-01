"""Attach realized demand to predictions and calculate production metrics."""
from __future__ import annotations

import os
from datetime import datetime, timezone
import httpx

URL = os.getenv("SUPABASE_URL", "").rstrip("/")
KEY = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY", "")


def stamp(value: str) -> str:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()


def main() -> None:
    if not URL or not KEY:
        raise SystemExit("SUPABASE_URL and SUPABASE_SERVICE_KEY are required")
    headers = {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
    with httpx.Client(timeout=120) as db:
        predictions = db.get(f"{URL}/rest/v1/prediction_records", headers=headers, params={"select":"*", "actual_value":"is.null", "limit":"10000"})
        predictions.raise_for_status(); predictions = predictions.json()
        if not predictions:
            print("predictions_to_evaluate=0"); return
        demand = db.get(f"{URL}/rest/v1/demand_observations", headers=headers, params={"select":"station_id,observed_at,demand", "limit":"100000"})
        demand.raise_for_status()
        actuals = {(str(r["station_id"]), stamp(str(r["observed_at"]))): float(r["demand"]) for r in demand.json()}
        evaluated = []
        now = datetime.now(timezone.utc).isoformat()
        for row in predictions:
            key = (str(row["station_id"]), stamp(str(row["target_at"])))
            if key not in actuals: continue
            value = actuals[key]
            response = db.patch(
                f"{URL}/rest/v1/prediction_records",
                headers={**headers, "Prefer": "return=minimal"},
                params={"prediction_id": f"eq.{row['prediction_id']}"},
                json={"actual_value": value, "evaluated_at": now},
            )
            response.raise_for_status(); evaluated.append((value, float(row["predicted_value"])))
        if evaluated:
            # Match the official leaderboard: WAPE per station, then average.
            station_pairs = {}
            evaluated_rows = [
                row for row in predictions
                if (str(row["station_id"]), stamp(str(row["target_at"]))) in actuals
            ]
            for row, (actual, predicted) in zip(evaluated_rows, evaluated, strict=True):
                station_pairs.setdefault(str(row["station_id"]), []).append((actual, predicted))
            station_wapes = [
                sum(abs(actual - predicted) for actual, predicted in pairs) /
                sum(abs(actual) for actual, _ in pairs)
                for pairs in station_pairs.values()
                if sum(abs(actual) for actual, _ in pairs)
            ]
            wape = sum(station_wapes) / len(station_wapes) if station_wapes else None
            accuracy = max(0.0, 1.0 - wape) * 100 if wape is not None else None
            version = str(predictions[0]["model_version"])
            metrics = {"model_version": version, "metric_scope": "production", "accuracy": accuracy, "wape": wape, "coverage": len(evaluated) / len(predictions), "metadata": {"evaluated_predictions": len(evaluated)}}
            response = db.post(f"{URL}/rest/v1/model_metrics", headers={**headers, "Prefer": "return=minimal"}, json=metrics)
            response.raise_for_status()
            print(f"predictions_evaluated={len(evaluated)} wape={wape:.6f} accuracy={accuracy:.4f}")
        else:
            print("predictions_to_evaluate=0 actuals_not_released=1")


if __name__ == "__main__": main()
