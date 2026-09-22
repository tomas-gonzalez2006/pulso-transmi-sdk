import { NextResponse } from "next/server";

const supabaseUrl = (process.env.SUPABASE_URL ?? "").replace(/\/$/, "");

async function supabase(table: string, query: string): Promise<Record<string, unknown>[]> {
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!supabaseUrl || !key) return [];
  const response = await fetch(`${supabaseUrl}/rest/v1/${table}?${query}`, { headers: { apikey: key, Authorization: `Bearer ${key}` }, cache: "no-store" });
  return response.ok ? response.json() as Promise<Record<string, unknown>[]> : [];
}

export async function GET() {
  const [stations, predictions] = await Promise.all([
    supabase("stations", "select=station_id,station_name,latitude,longitude,corridor_id&order=station_id"),
    supabase("prediction_records", "select=station_id,target_at,predicted_value,model_version&order=target_at.asc&limit=5000")
  ]);
  const times = [...new Set(predictions.map((row) => String(row.target_at)))].sort();
  return NextResponse.json({ stations, predictions, times, generated_at: new Date().toISOString() }, { headers: { "Cache-Control": "no-store" } });
}
