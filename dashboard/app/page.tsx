"use client";

import { useEffect, useState } from "react";

type LeaderboardEntry = { display_name: string; accuracy: number; coverage: number; rank: number };
type Dashboard = {
  generated_at: string; identity?: { display_name?: string } | null; api_health?: { status?: string } | null;
  connection: { pulso_key_configured: boolean; supabase_configured: boolean };
  leaderboard?: { data?: LeaderboardEntry[] } | null; accuracy: number | null; coverage: number | null; drift: number | null;
  drift_detail: { recent_mean: number | null; baseline_mean: number | null; recent_count: number; baseline_count: number };
  runs: Array<Record<string, unknown>>; prediction_count: number; evaluated_count: number; latest_run?: Record<string, unknown> | null;
  errors_by_station: Record<string, { actual: number[]; predicted: number[] }>;
};

const number = (value: number | null, digits = 1) => value === null ? "—" : value.toFixed(digits);
const percent = (value: number | null) => value === null ? "—" : `${value.toFixed(1)}%`;

export default function Home() {
  const [data, setData] = useState<Dashboard | null>(null); const [error, setError] = useState("");
  useEffect(() => { fetch("/api/dashboard").then((r) => r.ok ? r.json() : Promise.reject()).then(setData).catch(() => setError("No se pudo cargar el panel.")); }, []);
  if (error) return <main className="shell"><div className="alert">{error} Revisa las variables privadas de Vercel.</div></main>;
  if (!data) return <main className="shell"><div className="loading">Cargando observabilidad…</div></main>;
  const stations = Object.entries(data.errors_by_station).map(([station, values]) => { const total = values.actual.reduce((a, b) => a + Math.abs(b), 0); const wape = total ? values.actual.reduce((s, v, i) => s + Math.abs(v - values.predicted[i]), 0) / total : null; return { station, accuracy: wape === null ? null : Math.max(0, 1 - wape) * 100 }; }).sort((a, b) => (a.accuracy ?? -1) - (b.accuracy ?? -1));
  return <main className="shell">
    <header><div><p className="eyebrow">PULSO TRANSMI / MLOPS</p><h1>Observabilidad del modelo</h1><p className="sub">Demanda, desempeño, drift y operación en un solo lugar.</p></div><div className="status"><span className={data.api_health?.status === "ok" ? "dot good" : "dot bad"} /> API {data.api_health?.status === "ok" ? "operativa" : "sin confirmar"}<small>{data.identity?.display_name ?? "Identidad no disponible"}</small></div></header>
    <section className="cards"><Metric label="Accuracy" value={percent(data.accuracy)} note={data.evaluated_count ? `${data.evaluated_count} targets evaluados` : "Aún sin ground truth"} /><Metric label="Cobertura" value={percent(data.coverage)} note={data.prediction_count ? `${data.prediction_count} predicciones guardadas` : "Sin predicciones registradas"} /><Metric label="Drift 7d vs 7d" value={percent(data.drift)} note={data.drift === null ? "Sin ventanas comparables" : data.drift > 20 ? "Revisar y considerar reentrenamiento" : "Dentro de vigilancia"} /><Metric label="Última ejecución" value={data.latest_run?.status ? String(data.latest_run.status) : "—"} note={data.latest_run?.started_at ? new Date(String(data.latest_run.started_at)).toLocaleString("es-CO") : "Sin ejecuciones"} /></section>
    {(!data.connection.pulso_key_configured || !data.connection.supabase_configured) && <div className="alert">Conexión incompleta: configura las variables privadas de Production en Vercel.</div>}
    <section className="grid"><Panel title="Drift de demanda" caption="Media reciente frente a la ventana anterior."><div className="bars"><div><span>Anterior</span><i style={{ height: `${Math.min(100, (data.drift_detail.baseline_mean ?? 0) / Math.max(data.drift_detail.baseline_mean ?? 1, data.drift_detail.recent_mean ?? 1) * 100)}%` }} /><b>{number(data.drift_detail.baseline_mean, 0)}</b></div><div><span>Últimos 7 días</span><i className="accent" style={{ height: `${Math.min(100, (data.drift_detail.recent_mean ?? 0) / Math.max(data.drift_detail.baseline_mean ?? 1, data.drift_detail.recent_mean ?? 1) * 100)}%` }} /><b>{number(data.drift_detail.recent_mean, 0)}</b></div></div><p className="muted">{data.drift_detail.recent_count} recientes · {data.drift_detail.baseline_count} de referencia</p></Panel><Panel title="Accuracy por estación" caption="Calculada con WAPE evaluado."><div className="table">{stations.length ? stations.map((row) => <div className="row" key={row.station}><span>{row.station}</span><span className={row.accuracy !== null && row.accuracy < 88 ? "warn" : "goodText"}>{percent(row.accuracy)}</span></div>) : <p className="muted">Todavía no hay predicciones evaluadas.</p>}</div></Panel></section>
    <section className="grid"><Panel title="Leaderboard" caption="Accuracy acumulada · API oficial Pulso TransMi."><Leaderboard entries={data.leaderboard?.data ?? []} identity={data.identity?.display_name} /></Panel><Panel title="Salud del pipeline" caption="Últimas ejecuciones persistidas en Supabase."><div className="table">{data.runs.length ? data.runs.slice(0, 8).map((run, i) => <div className="row" key={String(run.run_id ?? i)}><span>{String(run.run_type ?? "run")}<small>{run.started_at ? new Date(String(run.started_at)).toLocaleString("es-CO") : ""}</small></span><span className={run.status === "succeeded" ? "goodText" : run.status === "failed" ? "warn" : ""}>{String(run.status ?? "—")}</span></div>) : <p className="muted">Aún no hay ejecuciones registradas.</p>}</div></Panel></section>
    <footer>Actualizado {new Date(data.generated_at).toLocaleString("es-CO")} · Las credenciales nunca llegan al navegador.</footer>
  </main>;
}

function Metric({ label, value, note }: { label: string; value: string; note: string }) { return <article className="metric"><span>{label}</span><strong>{value}</strong><small>{note}</small></article>; }
function Panel({ title, caption, children }: { title: string; caption: string; children: React.ReactNode }) { return <article className="panel"><h2>{title}</h2><p className="caption">{caption}</p>{children}</article>; }
function Leaderboard({ entries, identity }: { entries: LeaderboardEntry[]; identity?: string }) {
  if (!entries.length) return <p className="muted">No hay resultados publicados todavía.</p>;
  const top = entries.slice(0, 10); const max = Math.max(...top.map((entry) => entry.accuracy), 1);
  return <div className="leaderboard">{top.map((entry) => { const mine = entry.display_name === identity; return <div className={`leader-row ${mine ? "mine" : ""}`} key={`${entry.rank}-${entry.display_name}`}><div className="leader-label"><span className="rank">#{entry.rank}</span><span className="leader-name">{entry.display_name}{mine && <em> tú</em>}<small>Cobertura {percent(entry.coverage * 100)}</small></span><strong>{percent(entry.accuracy)}</strong></div><div className="track"><i style={{ width: `${Math.max(2, entry.accuracy / max * 100)}%` }} /></div></div>; })}</div>;
}
