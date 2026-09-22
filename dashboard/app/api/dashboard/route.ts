import { NextResponse } from "next/server";

const apiUrl = (process.env.PULSO_API_URL ?? "https://pulso-transmi.72-60-245-2.sslip.io").replace(/\/$/, "");
const supabaseUrl = (process.env.SUPABASE_URL ?? "").replace(/\/$/, "");

type Row = Record<string, unknown>;

async function supabase(table: string, query: string): Promise<Row[]> {
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!supabaseUrl || !key) return [];
  const response = await fetch(`${supabaseUrl}/rest/v1/${table}?${query}`, {
    headers: { apikey: key, Authorization: `Bearer ${key}` },
    cache: "no-store"
  });
  if (!response.ok) return [];
  return response.json() as Promise<Row[]>;
}

async function pulso(path: string): Promise<unknown> {
  const key = process.env.PULSO_API_KEY;
  if (!key) return null;
  const response = await fetch(`${apiUrl}${path}`, {
    headers: { Authorization: `Bearer ${key}`, Accept: "application/json" },
    cache: "no-store"
  });
  if (!response.ok) return null;
  return response.json();
}

function mean(values: number[]) { return values.length ? values.reduce((a, b) => a + b, 0) / values.length : null; }
function wape(actual: number[], predicted: number[]) {
  const denominator = actual.reduce((a, b) => a + Math.abs(b), 0);
  return denominator ? actual.reduce((sum, value, i) => sum + Math.abs(value - predicted[i]), 0) / denominator : null;
}

export async function GET() {
  const [runs, metrics, predictions, demand, me, leaderboard, health] = await Promise.all([
    supabase("pipeline_runs", "select=*&order=started_at.desc&limit=30"),
    supabase("model_metrics", "select=*&order=measured_at.desc&limit=100"),
    supabase("prediction_records", "select=*&order=target_at.desc&limit=1000"),
    supabase("demand_observations", "select=station_id,observed_at,demand&order=observed_at.desc&limit=20000"),
    pulso("/v1/me"), pulso("/v1/leaderboard?window=cumulative"), pulso("/health")
  ]);

  const now = Date.now();
  const recent = demand.filter((r) => now - Date.parse(String(r.observed_at)) <= 7 * 86400000).map((r) => Number(r.demand)).filter(Number.isFinite);
  const baseline = demand.filter((r) => { const age = now - Date.parse(String(r.observed_at)); return age > 7 * 86400000 && age <= 14 * 86400000; }).map((r) => Number(r.demand)).filter(Number.isFinite);
  const recentMean = mean(recent); const baselineMean = mean(baseline);
  const drift = recentMean !== null && baselineMean ? Math.abs(recentMean - baselineMean) / Math.abs(baselineMean) : null;

  const evaluated = predictions.filter((r) => r.actual_value !== null && r.actual_value !== undefined);
  const actual = evaluated.map((r) => Number(r.actual_value));
  const predicted = evaluated.map((r) => Number(r.predicted_value));
  const calculatedWape = wape(actual, predicted);
  const calculatedAccuracy = calculatedWape === null ? null : Math.max(0, 1 - calculatedWape) * 100;
  const latestRun = runs[0] ?? null;

  return NextResponse.json({
    generated_at: new Date().toISOString(),
    identity: me,
    api_health: health,
    leaderboard,
    runs,
    metrics,
    prediction_count: predictions.length,
    evaluated_count: evaluated.length,
    accuracy: calculatedAccuracy ?? (metrics.find((r) => r.metric_scope === "rolling_24h")?.accuracy ?? null),
    coverage: predictions.length ? evaluated.length / predictions.length * 100 : null,
    drift: drift === null ? (metrics.find((r) => r.metric_scope === "drift")?.drift_score ?? null) : drift * 100,
    drift_detail: { recent_mean: recentMean, baseline_mean: baselineMean, recent_count: recent.length, baseline_count: baseline.length },
    latest_run: latestRun,
    errors_by_station: evaluated.reduce<Record<string, { actual: number[]; predicted: number[] }>>((acc, row) => {
      const station = String(row.station_id); acc[station] ??= { actual: [], predicted: [] };
      acc[station].actual.push(Number(row.actual_value)); acc[station].predicted.push(Number(row.predicted_value)); return acc;
    }, {})
  }, { headers: { "Cache-Control": "no-store" } });
}
