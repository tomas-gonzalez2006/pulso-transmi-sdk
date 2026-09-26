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

function myLeaderboardRow(payload: unknown, displayName: string | undefined): Row | null {
  if (!displayName || !payload || typeof payload !== "object") return null;
  const entries = Array.isArray(payload) ? payload : (payload as { data?: unknown }).data;
  if (!Array.isArray(entries)) return null;
  return (entries.find((entry) => entry && typeof entry === "object" && (entry as Row).display_name === displayName) as Row | undefined) ?? null;
}

function metricStudent(metric: Row): string | null {
  return ((metric.metadata as Row | undefined)?.student as string | undefined) ?? null;
}

export async function GET() {
  const [runs, metrics, predictions, demand, me, leaderboard, health] = await Promise.all([
    supabase("pipeline_runs", "select=*&order=started_at.desc&limit=30"),
    supabase("model_metrics", "select=*&order=measured_at.desc&limit=1000"),
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
  const leaderboardMine = myLeaderboardRow(leaderboard, String((me as Row | null)?.display_name ?? ""));
  const leaderboardMetrics = metrics.filter((row) => row.model_version === "pulso-leaderboard:cumulative");
  const myName = String((me as Row | null)?.display_name ?? "");
  const myLeaderboardMetrics = leaderboardMetrics.filter((row) => metricStudent(row) === myName);
  const latestLeaderboardMetric = myLeaderboardMetrics[0] ?? null;
  const previousLeaderboardMetric = myLeaderboardMetrics[1] ?? null;
  const leaderboardAccuracy = Number.isFinite(Number(leaderboardMine?.accuracy))
    ? Number(leaderboardMine?.accuracy)
    : Number(latestLeaderboardMetric?.accuracy);
  const leaderboardCoverage = Number.isFinite(Number(leaderboardMine?.coverage))
    ? Number(leaderboardMine?.coverage) * 100
    : Number(latestLeaderboardMetric?.coverage) * 100;
  const leaderboardDrift = Number.isFinite(Number(latestLeaderboardMetric?.drift_score))
    ? Number(latestLeaderboardMetric?.drift_score)
    : latestLeaderboardMetric && previousLeaderboardMetric
      ? Number(latestLeaderboardMetric.accuracy) - Number(previousLeaderboardMetric.accuracy)
      : null;
  const leaderboardHistory = leaderboardMetrics
    .map((row) => ({ student: metricStudent(row), measured_at: row.measured_at, accuracy: Number(row.accuracy) }))
    .filter((row) => row.student && Number.isFinite(row.accuracy));

  return NextResponse.json({
    generated_at: new Date().toISOString(),
    identity: me,
    api_health: health,
    leaderboard,
    leaderboard_me: leaderboardMine,
    connection: {
      pulso_key_configured: Boolean(process.env.PULSO_API_KEY),
      supabase_configured: Boolean(supabaseUrl && process.env.SUPABASE_SERVICE_ROLE_KEY),
      leaderboard_loaded: leaderboard !== null,
      identity_loaded: me !== null,
    },
    runs,
    metrics,
    prediction_count: predictions.length,
    evaluated_count: evaluated.length,
    accuracy: Number.isFinite(leaderboardAccuracy) ? leaderboardAccuracy : (calculatedAccuracy ?? null),
    coverage: Number.isFinite(leaderboardCoverage) ? leaderboardCoverage : null,
    leaderboard_drift: Number.isFinite(Number(leaderboardDrift)) ? leaderboardDrift : null,
    leaderboard_history: leaderboardHistory,
    drift: drift === null ? (metrics.find((r) => r.metric_scope === "drift")?.drift_score ?? null) : drift * 100,
    drift_detail: { recent_mean: recentMean, baseline_mean: baselineMean, recent_count: recent.length, baseline_count: baseline.length },
    latest_run: latestRun,
    errors_by_station: evaluated.reduce<Record<string, { actual: number[]; predicted: number[] }>>((acc, row) => {
      const station = String(row.station_id); acc[station] ??= { actual: [], predicted: [] };
      acc[station].actual.push(Number(row.actual_value)); acc[station].predicted.push(Number(row.predicted_value)); return acc;
    }, {})
  }, { headers: { "Cache-Control": "no-store" } });
}
