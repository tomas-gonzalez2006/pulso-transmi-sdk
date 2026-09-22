"use client";

import { useEffect, useRef, useState } from "react";
import type { Map as LeafletMap } from "leaflet";
import "leaflet/dist/leaflet.css";
import "./map.css";

type Station = { station_id: string; station_name: string; latitude: number; longitude: number; corridor_id?: string };
type Prediction = { station_id: string; predicted_value: number };

export default function StationLeafletMap({ stations, predictions, selectedId, onSelect }: { stations: Station[]; predictions: Prediction[]; selectedId?: string; onSelect: (station: Station) => void }) {
  const node = useRef<HTMLDivElement>(null); const mapRef = useRef<LeafletMap | null>(null); const layerRef = useRef<import("leaflet").LayerGroup | null>(null); const [mapReady, setMapReady] = useState(false);
  useEffect(() => {
    let disposed = false;
    import("leaflet").then((L) => {
      if (disposed || !node.current || mapRef.current) return;
      const map = L.map(node.current, { zoomControl: true }).setView([4.66, -74.10], 11);
      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { attribution: "© OpenStreetMap contributors", maxZoom: 19 }).addTo(map);
      mapRef.current = map; layerRef.current = L.layerGroup().addTo(map); setMapReady(true); window.setTimeout(() => map.invalidateSize(), 0);
    });
    return () => { disposed = true; mapRef.current?.remove(); mapRef.current = null; };
  }, []);
  useEffect(() => {
    if (!mapRef.current || !layerRef.current) return;
    import("leaflet").then((L) => {
      if (!layerRef.current) return;
      layerRef.current.clearLayers(); const max = Math.max(...predictions.map((row) => row.predicted_value), 1); const values = new Map(predictions.map((row) => [row.station_id, row.predicted_value]));
      stations.forEach((station) => { const value = values.get(station.station_id); const selected = station.station_id === selectedId; const color = value === undefined ? "#758e89" : `hsl(${Math.max(8, 150 - value / max * 142)} 72% 43%)`; const marker = L.circleMarker([station.latitude, station.longitude], { radius: selected ? 11 : 8, color: selected ? "#102a2c" : "#fff", weight: selected ? 4 : 2, fillColor: color, fillOpacity: .95 }); marker.bindTooltip(`${station.station_name}${value === undefined ? "" : ` · ${Math.round(value)} pasajeros`}`, { direction: "top" }); marker.on("click", () => onSelect(station)); marker.addTo(layerRef.current!); });
    });
  }, [stations, predictions, selectedId, onSelect, mapReady]);
  return <div ref={node} className="leaflet-map" aria-label="Mapa de estaciones de Bogotá" />;
}
