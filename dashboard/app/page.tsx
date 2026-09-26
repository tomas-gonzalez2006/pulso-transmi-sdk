"use client";

import { useEffect, useState } from "react";
import Sidebar from "./components/Sidebar";

type LeaderboardEntry = { display_name: string; accuracy: number; coverage: number; rank: number };
type LeaderboardPoint = { student: string; measured_at: string; accuracy: number };
type Dashboard = {
  generated_at: string;
  identity?: { display_name?: string } | null;
  api_health?: { status?: string } | null;
  connection: { pulso_key_configured: boolean; supabase_configured: boolean };
  leaderboard?: { data?: LeaderboardEntry[] } | null;
  leaderboard_me?: LeaderboardEntry | null;
  leaderboard_history: LeaderboardPoint[];
  accuracy: number | null;
  coverage: number | null;
  leaderboard_drift: number | null;
  drift: number | null;
  drift_detail: { recent_mean: number | null; baseline_mean: number | null; recent_count: number; baseline_count: number };
  runs: Array<Record<string, unknown>>;
  prediction_count: number;
  evaluated_count: number;
  errors_by_station: Record<string, { actual: number[]; predicted: number[] }>;
};

const number = (value: number | null, digits = 1) => value === null ? "—" : value.toFixed(digits);
const percent = (value: number | null) => value === null ? "—" : `${value.toFixed(1)}%`;

export default function Home() {
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    fetch("/api/dashboard").then((r) => r.ok ? r.json() : Promise.reject()).then(setData).catch(() => setError("No se pudo cargar el panel. Revisa las variables privadas de Vercel."));
  }, []);
  if (error) return <main className="shell"><div className="alert">{error}</div></main>;
  if (!data) return <main className="shell"><div className="loading">Cargando observabilidad…</div></main>;

  const stations = Object.entries(data.errors_by_station).map(([station, values]) => {
    const total = values.actual.reduce((a, b) => a + Math.abs(b), 0);
    const errorValue = total ? values.actual.reduce((s, v, i) => s + Math.abs(v - values.predicted[i]), 0) / total : null;
    return { station, accuracy: errorValue === null ? null : Math.max(0, 1 - errorValue) * 100 };
  }).sort((a, b) => (a.accuracy ?? -1) - (b.accuracy ?? -1));
  const driftLabel = data.leaderboard_drift === null ? "—" : `${data.leaderboard_drift >= 0 ? "+" : ""}${data.leaderboard_drift.toFixed(1)} pp`;

  return <><Sidebar /><main className="shell with-sidebar">
    <header><div><p className="eyebrow">PULSO TRANSMI / MLOPS</p><h1>Observabilidad del modelo</h1><p className="sub">Demanda, desempeño, drift y operación en un solo lugar.</p></div><div className="status"><span className={data.api_health?.status === "ok" ? "dot good" : "dot bad"} /> API {data.api_health?.status === "ok" ? "operativa" : "sin confirmar"}<small>{data.identity?.display_name ?? "Identidad no disponible"}</small></div></header>
    <section className="cards">
      <Metric label="Accuracy" value={percent(data.accuracy)} note="Accuracy acumulada · API oficial" />
      <Metric label="Cobertura total" value={percent(data.coverage)} note="Cobertura acumulada · API oficial" />
      <Metric label="Drift accuracy" value={driftLabel} note={data.leaderboard_drift === null ? "Sin periodo anterior" : "Contra el periodo inmediatamente anterior"} />
      <Metric label="Drift demanda" value={percent(data.drift)} note={data.drift === null ? "Sin ventanas comparables" : "Últimos 7 días vs anteriores"} />
    </section>
    {(!data.connection.pulso_key_configured || !data.connection.supabase_configured) && <div className="alert">Conexión incompleta: configura las variables privadas de Production en Vercel.</div>}
    <section className="grid"><Panel title="Drift de demanda" caption="Media reciente frente a la ventana anterior."><div className="bars"><div><span>Anterior</span><i style={{ height: `${Math.min(100, (data.drift_detail.baseline_mean ?? 0) / Math.max(data.drift_detail.baseline_mean ?? 1, data.drift_detail.recent_mean ?? 1) * 100)}%` }} /><b>{number(data.drift_detail.baseline_mean, 0)}</b></div><div><span>Últimos 7 días</span><i className="accent" style={{ height: `${Math.min(100, (data.drift_detail.recent_mean ?? 0) / Math.max(data.drift_detail.baseline_mean ?? 1, data.drift_detail.recent_mean ?? 1) * 100)}%` }} /><b>{number(data.drift_detail.recent_mean, 0)}</b></div></div><p className="muted">{data.drift_detail.recent_count} recientes · {data.drift_detail.baseline_count} de referencia</p></Panel><Panel title="Calidad de evaluación" caption="Estado del ground truth de tus predicciones."><EvaluationStatus data={data} /></Panel></section>
    <section className="grid"><Panel title="Rendimiento del leaderboard" caption="Accuracy acumulada · tu posición siempre visible."><Leaderboard entries={data.leaderboard?.data ?? []} identity={data.identity?.display_name} mine={data.leaderboard_me} /></Panel><Panel title="Resumen de desempeño" caption="Indicadores disponibles de la API oficial."><PerformanceSummary data={data} /></Panel></section>
    <footer>Actualizado {new Date(data.generated_at).toLocaleString("es-CO")} · Las credenciales nunca llegan al navegador.</footer>
  </main></>;
}

function Metric({ label, value, note }: { label: string; value: string; note: string }) { return <article className="metric"><span>{label}</span><strong>{value}</strong><small>{note}</small></article>; }
function Panel({ title, caption, children }: { title: string; caption: string; children: React.ReactNode }) { return <article className="panel"><h2>{title}</h2><p className="caption">{caption}</p>{children}</article>; }
function Leaderboard({ entries, identity, mine }: { entries: LeaderboardEntry[]; identity?: string; mine?: LeaderboardEntry | null }) {
  if (!entries.length && !mine) return <p className="muted">No hay resultados publicados todavía.</p>;
  const top = entries.slice(0, 10);
  const visible = mine && !top.some((entry) => entry.display_name === mine.display_name) ? [...top, mine] : top;
  const max = Math.max(...visible.map((entry) => entry.accuracy), 1);
  return <div className="leaderboard">{visible.map((entry) => { const isMine = entry.display_name === identity; return <div className={`leader-row ${isMine ? "mine" : ""}`} key={`${entry.rank}-${entry.display_name}`}><div className="leader-label"><span className="rank">#{entry.rank}</span><span className="leader-name">{entry.display_name}{isMine && <em> tú</em>}<small>Cobertura {percent(entry.coverage * 100)}</small></span><strong>{percent(entry.accuracy)}</strong></div><div className="track"><i style={{ width: `${Math.max(2, entry.accuracy / max * 100)}%` }} /></div></div>; })}</div>;
}

function PerformanceSummary({ data }: { data: Dashboard }) {
  const rank = data.leaderboard_me?.rank;
  const drift = data.leaderboard_drift;
  return <div className="table">
    <div className="row"><span>Posición actual</span><strong>{rank ? `#${rank}` : "—"}</strong></div>
    <div className="row"><span>Accuracy acumulada</span><strong>{percent(data.accuracy)}</strong></div>
    <div className="row"><span>Cobertura total</span><strong>{percent(data.coverage)}</strong></div>
    <div className="row"><span>Cambio vs. periodo anterior</span><strong className={drift !== null && drift < 0 ? "warn" : "goodText"}>{drift === null ? "—" : `${drift >= 0 ? "+" : ""}${drift.toFixed(1)} pp`}</strong></div>
    <p className="muted">{drift === null ? "Esperando el siguiente snapshot del leaderboard." : drift >= 0 ? "El desempeño está mejorando." : "El desempeño está disminuyendo; conviene revisar el próximo ciclo."}</p>
  </div>;
}

function EvaluationStatus({ data }: { data: Dashboard }) {
  const pending = Math.max(0, data.prediction_count - data.evaluated_count);
  const evaluatedShare = data.prediction_count ? data.evaluated_count / data.prediction_count * 100 : 0;
  return <div className="table">
    <div className="row"><span>Predicciones guardadas</span><strong>{data.prediction_count}</strong></div>
    <div className="row"><span>Con resultado real</span><strong>{data.evaluated_count}</strong></div>
    <div className="row"><span>Pendientes de evaluar</span><strong>{pending}</strong></div>
    <div className="row"><span>Cobertura de evaluación</span><strong>{percent(evaluatedShare)}</strong></div>
    <p className="muted">{pending ? "El profesor aún no ha publicado todos los valores reales." : "Todas las predicciones disponibles tienen resultado real."}</p>
  </div>;
}

function LeaderboardChart({ history, identity }: { history: LeaderboardPoint[]; identity?: string }) {
  const students = [...new Set(history.map((point) => point.student))];
  if (!history.length) return <p className="muted">Aún no hay snapshots del leaderboard.</p>;
  const ordered = [...history].sort((a, b) => Date.parse(a.measured_at) - Date.parse(b.measured_at));
  const minTime = Date.parse(ordered[0].measured_at); const maxTime = Date.parse(ordered[ordered.length - 1].measured_at) || minTime + 1;
  const minValue = Math.max(0, Math.floor(Math.min(...history.map((point) => point.accuracy)) - 2));
  const maxValue = Math.min(100, Math.ceil(Math.max(...history.map((point) => point.accuracy)) + 2));
  const width = 720; const height = 280; const x = (time: number) => 38 + ((time - minTime) / Math.max(1, maxTime - minTime)) * (width - 58); const y = (value: number) => height - 28 - ((value - minValue) / Math.max(1, maxValue - minValue)) * (height - 48);
  return <div><svg className="leader-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Evolución del accuracy del leaderboard">{[minValue, (minValue + maxValue) / 2, maxValue].map((value) => <g key={value}><line x1="38" x2={width - 20} y1={y(value)} y2={y(value)} className="chart-grid" /><text x="0" y={y(value) + 4} className="chart-label">{value.toFixed(0)}%</text></g>)}{students.map((student) => { const points = ordered.filter((point) => point.student === student); const mine = student === identity; return <polyline key={student} points={points.map((point) => `${x(Date.parse(point.measured_at))},${y(point.accuracy)}`).join(" ")} className={mine ? "chart-line mine-line" : "chart-line"} />; })}</svg><div className="chart-legend">{students.map((student) => <span key={student} className={student === identity ? "mine-legend" : ""}><i />{student === identity ? "Tú" : student}</span>)}</div></div>;
}
