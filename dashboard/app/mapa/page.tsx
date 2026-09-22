"use client";

import { useEffect, useMemo, useState } from "react";
import Sidebar from "../components/Sidebar";

type Station = { station_id: string; station_name: string; latitude: number; longitude: number; corridor_id: string };
type Prediction = { station_id: string; target_at: string; predicted_value: number; model_version: string };
type MapData = { stations: Station[]; predictions: Prediction[]; times: string[]; generated_at: string };

const timeLabel = (value: string) => new Date(value).toLocaleString("es-CO", { dateStyle: "short", timeStyle: "short" });
const valueColor = (value: number, max: number) => `hsl(${Math.max(8, 150 - value / Math.max(max, 1) * 142)} 72% 43%)`;

export default function MapPage() {
  const [data, setData] = useState<MapData | null>(null); const [selectedTime, setSelectedTime] = useState(""); const [selected, setSelected] = useState<Station | null>(null);
  useEffect(() => { fetch("/api/map").then((r) => r.json()).then((payload: MapData) => { setData(payload); setSelectedTime(payload.times[0] ?? ""); }).catch(() => setData({ stations: [], predictions: [], times: [], generated_at: "" })); }, []);
  const predictionsAtTime = useMemo(() => data?.predictions.filter((row) => row.target_at === selectedTime) ?? [], [data, selectedTime]);
  const byStation = new Map(predictionsAtTime.map((row) => [row.station_id, row])); const max = Math.max(...predictionsAtTime.map((row) => Number(row.predicted_value)), 1);
  const bounds = data?.stations.reduce((acc, station) => ({ minLat: Math.min(acc.minLat, station.latitude), maxLat: Math.max(acc.maxLat, station.latitude), minLon: Math.min(acc.minLon, station.longitude), maxLon: Math.max(acc.maxLon, station.longitude) }), { minLat: 90, maxLat: -90, minLon: 180, maxLon: -180 }) ?? { minLat: 4.5, maxLat: 4.8, minLon: -74.3, maxLon: -73.9 };
  const x = (longitude: number) => 40 + (longitude - bounds.minLon) / Math.max(bounds.maxLon - bounds.minLon, .001) * 720;
  const y = (latitude: number) => 420 - (latitude - bounds.minLat) / Math.max(bounds.maxLat - bounds.minLat, .001) * 350;
  const selectedPrediction = selected ? byStation.get(selected.station_id) : undefined;
  return <><Sidebar /><main className="shell with-sidebar"><header><div><p className="eyebrow">PULSO TRANSMI / GEODATA</p><h1>Mapa de estaciones</h1><p className="sub">Explora la predicción del modelo por estación y horizonte.</p></div><div className="time-picker"><label>Instante de predicción<select value={selectedTime} onChange={(event) => setSelectedTime(event.target.value)}><option value="">Sin predicciones disponibles</option>{data?.times.map((time) => <option key={time} value={time}>{timeLabel(time)}</option>)}</select></label></div></header>
    {!data ? <div className="loading">Cargando estaciones…</div> : <section className="map-layout"><article className="panel map-panel"><div className="map-legend"><span><i className="legend-low" /> Baja</span><span><i className="legend-high" /> Alta</span><small>{predictionsAtTime.length} predicciones en este instante</small></div><svg className="station-map" viewBox="0 0 800 460" role="img" aria-label="Mapa de estaciones de TransMi">{data.stations.length ? data.stations.map((station) => { const prediction = byStation.get(station.station_id); const active = selected?.station_id === station.station_id; return <g key={station.station_id} className="station-point" onClick={() => setSelected(station)}><circle cx={x(station.longitude)} cy={y(station.latitude)} r={active ? 12 : 9} fill={prediction ? valueColor(Number(prediction.predicted_value), max) : "#9eb5b0"} stroke={active ? "#102a2c" : "#fff"} strokeWidth={active ? 4 : 2} /><text x={x(station.longitude) + 13} y={y(station.latitude) + 4}>{station.station_id}</text></g>; }) : <text x="250" y="220" className="empty-map">No hay estaciones disponibles</text>}</svg></article><aside className="panel station-detail">{selected ? <><p className="eyebrow">ESTACIÓN SELECCIONADA</p><h2>{selected.station_name}</h2><p className="muted">ID {selected.station_id} · Corredor {selected.corridor_id}</p><div className="prediction-value">{selectedPrediction ? Math.round(selectedPrediction.predicted_value) : "—"}<small> pasajeros estimados</small></div><p className="muted">{selectedTime ? timeLabel(selectedTime) : "Selecciona un instante"}</p><div className="detail-row"><span>Latitud</span><b>{selected.latitude.toFixed(5)}</b></div><div className="detail-row"><span>Longitud</span><b>{selected.longitude.toFixed(5)}</b></div></> : <div className="empty-detail"><div className="pin">⌖</div><h2>Selecciona una estación</h2><p className="muted">Haz clic en un punto del mapa para consultar la predicción.</p></div>}</aside></section>}
    <footer>Actualizado {data?.generated_at ? new Date(data.generated_at).toLocaleString("es-CO") : "—"} · Las credenciales nunca llegan al navegador.</footer></main></>;
}
