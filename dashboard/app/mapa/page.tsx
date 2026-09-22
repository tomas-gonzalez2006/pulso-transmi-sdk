"use client";

import { useEffect, useMemo, useState } from "react";
import Sidebar from "../components/Sidebar";
import StationLeafletMap from "./StationLeafletMap";

type Station = { station_id: string; station_name: string; latitude: number; longitude: number; corridor_id?: string };
type Prediction = { station_id: string; target_at: string; predicted_value: number; model_version: string };
type MapData = { stations: Station[]; predictions: Prediction[]; times: string[]; generated_at: string };
const timeLabel = (value: string) => new Date(value).toLocaleString("es-CO", { dateStyle: "short", timeStyle: "short" });

export default function MapPage() {
  const [data, setData] = useState<MapData | null>(null); const [selectedTime, setSelectedTime] = useState(""); const [selected, setSelected] = useState<Station | null>(null);
  useEffect(() => { fetch("/api/map").then((r) => r.json()).then((payload: MapData) => { setData(payload); setSelectedTime(payload.times[0] ?? ""); }).catch(() => setData({ stations: [], predictions: [], times: [], generated_at: "" })); }, []);
  const predictionsAtTime = useMemo(() => data?.predictions.filter((row) => row.target_at === selectedTime) ?? [], [data, selectedTime]);
  const selectedPrediction = selected ? predictionsAtTime.find((row) => row.station_id === selected.station_id) : undefined;
  return <><Sidebar /><main className="shell with-sidebar"><header><div><p className="eyebrow">PULSO TRANSMI / GEODATA</p><h1>Mapa de estaciones</h1><p className="sub">Ubica las estaciones en Bogotá y consulta la predicción del modelo.</p></div><div className="time-picker"><label>Instante de predicción<select value={selectedTime} onChange={(event) => setSelectedTime(event.target.value)}><option value="">Sin predicciones disponibles</option>{data?.times.map((time) => <option key={time} value={time}>{timeLabel(time)}</option>)}</select></label></div></header>
    {!data ? <div className="loading">Cargando mapa de Bogotá…</div> : <section className="map-layout"><article className="panel map-panel"><div className="map-legend"><span><i className="legend-low" /> Baja</span><span><i className="legend-high" /> Alta</span><small>{predictionsAtTime.length} predicciones en este instante</small></div><StationLeafletMap stations={data.stations} predictions={predictionsAtTime} selectedId={selected?.station_id} onSelect={setSelected} /></article><aside className="panel station-detail">{selected ? <><p className="eyebrow">ESTACIÓN SELECCIONADA</p><h2>{selected.station_name}</h2><p className="muted">ID {selected.station_id} · Corredor {selected.corridor_id}</p><div className="prediction-value">{selectedPrediction ? Math.round(selectedPrediction.predicted_value) : "—"}<small> pasajeros estimados</small></div><p className="muted">{selectedTime ? timeLabel(selectedTime) : "Selecciona un instante"}</p><div className="detail-row"><span>Latitud</span><b>{selected.latitude.toFixed(5)}</b></div><div className="detail-row"><span>Longitud</span><b>{selected.longitude.toFixed(5)}</b></div></> : <div className="empty-detail"><div className="pin">⌖</div><h2>Selecciona una estación</h2><p className="muted">Haz clic en un marcador del mapa para consultar la predicción.</p></div>}</aside></section>}
    <footer>Mapa © OpenStreetMap contributors · Actualizado {data?.generated_at ? new Date(data.generated_at).toLocaleString("es-CO") : "—"}</footer></main></>;
}
