"""Load the public Pulso TransMi API dataset into the linked Supabase project."""

from __future__ import annotations

import csv
import json
import re
import subprocess
import tempfile
import unicodedata
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen


API = "https://pulso-transmi.72-60-245-2.sslip.io"
SOURCE = "pulso-transmi-api"
ROOT = Path(__file__).resolve().parents[1]


def sql_text(value: str | None) -> str:
    if value is None:
        return "NULL"
    return "'" + value.replace("'", "''") + "'"


def sql_num(value: str | None) -> str:
    if value is None or value == "":
        return "NULL"
    return value


def sql_ts(value: str) -> str:
    return f"{sql_text(value)}::timestamptz"


def fetch_csv(name: str) -> list[dict[str, str]]:
    request = Request(f"{API}/v1/downloads/{name}", headers={"User-Agent": "pulso-transmi-loader/1.0"})
    with urlopen(request, timeout=60) as response:
        return list(csv.DictReader(response.read().decode("utf-8-sig").splitlines()))


def run_sql(sql: str) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".sql", encoding="utf-8", delete=False) as handle:
        handle.write(sql)
        path = handle.name
    try:
        result = subprocess.run(
            ["npx", "supabase", "db", "query", "--linked", "--file", path],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        return result.stdout
    finally:
        Path(path).unlink(missing_ok=True)


def chunks(rows: list[dict[str, str]], size: int = 2000):
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


def corridor_id(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")


def main() -> None:
    meta = json.loads(urlopen(f"{API}/v1/meta", timeout=30).read())
    stations = fetch_csv("stations.csv")
    observations = fetch_csv("observations.csv")
    context = fetch_csv("context.csv")
    started = datetime.now().astimezone().isoformat()

    batch_output = run_sql(
        "INSERT INTO public.ingestion_batches (source_name, api_version, started_at, cutoff_at, status) "
        f"VALUES ({sql_text(SOURCE)}, {sql_text(meta['api_version'])}, {sql_ts(started)}, "
        f"{sql_ts(meta['dataset']['history_end'])}, 'running') RETURNING ingestion_id;"
    )
    match = re.search(r"[|│]\s*(\d+)\s*[|│]", batch_output)
    if not match:
        raise RuntimeError(f"Could not parse ingestion id from CLI output: {batch_output}")
    ingestion_id = match.group(1)

    try:
        corridors = {}
        for row in stations:
            corridors[row["corridor"]] = corridor_id(row["corridor"])
        corridor_values = ",".join(
            f"({sql_text(cid)}, {sql_text(name)}, NULL, TRUE)" for name, cid in sorted(corridors.items())
        )
        run_sql(
            "INSERT INTO public.corridors (corridor_id, corridor_name, description, active) VALUES "
            f"{corridor_values} ON CONFLICT (corridor_id) DO UPDATE SET corridor_name=EXCLUDED.corridor_name, active=TRUE;"
        )

        station_values = ",".join(
            f"({sql_text(r['station_id'])}, {sql_text(corridors[r['corridor']])}, {sql_text(r['station_name'])}, "
            f"{sql_num(r['latitude'])}, {sql_num(r['longitude'])})"
            for r in stations
        )
        run_sql(
            "INSERT INTO public.stations (station_id, corridor_id, station_name, latitude, longitude) VALUES "
            f"{station_values} ON CONFLICT (station_id) DO UPDATE SET corridor_id=EXCLUDED.corridor_id, "
            "station_name=EXCLUDED.station_name, latitude=EXCLUDED.latitude, longitude=EXCLUDED.longitude;"
        )

        all_times = sorted({r["observed_at"] for r in observations} | {r["observed_at"] for r in context})
        time_values = []
        for raw in all_times:
            local = datetime.fromisoformat(raw)
            time_values.append(
                f"({sql_ts(raw)}, {sql_text(local.date().isoformat())}::date, {local.hour}, {local.minute}, "
                f"{local.isoweekday()}, {str(local.isoweekday() >= 6).upper()})"
            )
        for group in chunks([{"value": value} for value in time_values]):
            run_sql(
                "INSERT INTO public.time_slots (observed_at, local_date, local_hour, local_minute, weekday, is_weekend) VALUES "
                + ",".join(item["value"] for item in group)
                + " ON CONFLICT (observed_at) DO NOTHING;"
            )

        for group in chunks(observations):
            values = ",".join(
                f"({sql_text(r['station_id'])}, {sql_ts(r['observed_at'])}, {sql_num(r['demand'])}, {ingestion_id})"
                for r in group
            )
            run_sql(
                "INSERT INTO public.demand_observations (station_id, observed_at, demand, ingestion_id) VALUES "
                f"{values} ON CONFLICT (station_id, observed_at) DO UPDATE SET demand=EXCLUDED.demand, ingestion_id=EXCLUDED.ingestion_id;"
            )

        for group in chunks(context):
            weather_values = ",".join(
                f"({sql_ts(r['observed_at'])}, {sql_num(r['rain_mm'])}, {sql_num(r.get('rain_forecast'))}, "
                f"{sql_num(r.get('temperature_c'))}, {sql_num(r.get('temperature_forecast'))}, {ingestion_id})"
                for r in group
            )
            event_values = ",".join(
                f"({sql_ts(r['observed_at'])}, {sql_num(r['event_intensity'])}, NULL, NULL, {ingestion_id})"
                for r in group
            )
            run_sql(
                "INSERT INTO public.weather_observations (observed_at, rain_mm, rain_forecast, temperature_c, temperature_forecast, ingestion_id) VALUES "
                f"{weather_values} ON CONFLICT (observed_at) DO UPDATE SET rain_mm=EXCLUDED.rain_mm, rain_forecast=EXCLUDED.rain_forecast, "
                "temperature_c=EXCLUDED.temperature_c, temperature_forecast=EXCLUDED.temperature_forecast, ingestion_id=EXCLUDED.ingestion_id;"
            )
            run_sql(
                "INSERT INTO public.event_observations (observed_at, event_intensity, event_type, event_name, ingestion_id) VALUES "
                f"{event_values} ON CONFLICT (observed_at) DO UPDATE SET event_intensity=EXCLUDED.event_intensity, ingestion_id=EXCLUDED.ingestion_id;"
            )

        total = len(observations) + len(context) * 2 + len(stations)
        run_sql(
            f"UPDATE public.ingestion_batches SET finished_at=now(), status='succeeded', rows_loaded={total} "
            f"WHERE ingestion_id={ingestion_id};"
        )
        print(json.dumps({"status": "succeeded", "ingestion_id": int(ingestion_id), "stations": len(stations), "observations": len(observations), "context": len(context)}))
    except Exception as exc:
        run_sql(
            f"UPDATE public.ingestion_batches SET finished_at=now(), status='failed', error_message={sql_text(str(exc)[:1000])} "
            f"WHERE ingestion_id={ingestion_id};"
        )
        raise


if __name__ == "__main__":
    main()
