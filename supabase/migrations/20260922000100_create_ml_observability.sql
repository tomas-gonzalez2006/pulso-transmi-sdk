-- Observabilidad de inferencia, submissions, evaluación y drift.
-- La llave de servicio de Supabase es la única que debe escribir estas tablas.

create table if not exists public.pipeline_runs (
  run_id text primary key,
  run_type text not null check (run_type in ('collector', 'inference', 'evaluation', 'training')),
  status text not null check (status in ('running', 'succeeded', 'failed', 'skipped')),
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  cycle_id text,
  model_version text,
  git_commit text,
  records_processed integer not null default 0,
  error_message text,
  metadata jsonb not null default '{}'::jsonb
);

create index if not exists idx_pipeline_runs_started_at on public.pipeline_runs(started_at desc);
create index if not exists idx_pipeline_runs_type on public.pipeline_runs(run_type, started_at desc);

create table if not exists public.prediction_records (
  prediction_id bigint generated always as identity primary key,
  cycle_id text not null,
  submission_id text,
  station_id text not null references public.stations(station_id),
  target_at timestamptz not null,
  horizon_minutes smallint,
  predicted_value double precision not null check (predicted_value >= 0),
  actual_value double precision,
  model_version text not null,
  submitted_at timestamptz not null default now(),
  evaluated_at timestamptz,
  unique(cycle_id, station_id, target_at, model_version)
);

create index if not exists idx_prediction_records_target on public.prediction_records(target_at desc);
create index if not exists idx_prediction_records_cycle on public.prediction_records(cycle_id);

create table if not exists public.model_metrics (
  metric_id bigint generated always as identity primary key,
  measured_at timestamptz not null default now(),
  model_version text not null,
  metric_scope text not null check (metric_scope in ('validation', 'production', 'rolling_24h', 'cumulative', 'drift')),
  station_id text,
  horizon_minutes smallint,
  accuracy double precision,
  wape double precision,
  coverage double precision,
  drift_score double precision,
  reference_window text,
  metadata jsonb not null default '{}'::jsonb
);

create index if not exists idx_model_metrics_measured on public.model_metrics(measured_at desc);
create index if not exists idx_model_metrics_scope on public.model_metrics(metric_scope, measured_at desc);

alter table public.pipeline_runs enable row level security;
alter table public.prediction_records enable row level security;
alter table public.model_metrics enable row level security;

-- Lectura pública controlada para el dashboard sin exponer credenciales de escritura.
drop policy if exists dashboard_read_pipeline_runs on public.pipeline_runs;
create policy dashboard_read_pipeline_runs on public.pipeline_runs for select using (true);
drop policy if exists dashboard_read_prediction_records on public.prediction_records;
create policy dashboard_read_prediction_records on public.prediction_records for select using (true);
drop policy if exists dashboard_read_model_metrics on public.model_metrics;
create policy dashboard_read_model_metrics on public.model_metrics for select using (true);
