-- Pulso TransMi: esquema normalizado de datos (sin modelos ni predicciones).

create table if not exists public.corridors (
  corridor_id text primary key,
  corridor_name text not null unique,
  description text,
  active boolean not null default true
);

create table if not exists public.stations (
  station_id text primary key,
  corridor_id text not null references public.corridors(corridor_id),
  station_name text not null,
  latitude double precision not null check (latitude between -90 and 90),
  longitude double precision not null check (longitude between -180 and 180),
  valid_from date,
  valid_to date,
  check (valid_to is null or valid_from is null or valid_to >= valid_from)
);

create table if not exists public.time_slots (
  observed_at timestamptz primary key,
  local_date date not null,
  local_hour smallint not null check (local_hour between 0 and 23),
  local_minute smallint not null check (local_minute in (0, 15, 30, 45)),
  weekday smallint not null check (weekday between 1 and 7),
  is_weekend boolean not null
);

create table if not exists public.ingestion_batches (
  ingestion_id bigint generated always as identity primary key,
  source_name text not null,
  api_version text,
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  cutoff_at timestamptz,
  status text not null check (status in ('running', 'succeeded', 'failed', 'partial')),
  rows_loaded integer not null default 0 check (rows_loaded >= 0),
  error_message text
);

create table if not exists public.demand_observations (
  station_id text not null references public.stations(station_id),
  observed_at timestamptz not null references public.time_slots(observed_at),
  demand integer not null check (demand >= 0),
  ingestion_id bigint references public.ingestion_batches(ingestion_id),
  primary key (station_id, observed_at)
);

create table if not exists public.weather_observations (
  observed_at timestamptz primary key references public.time_slots(observed_at),
  rain_mm numeric(10, 4) not null check (rain_mm >= 0),
  rain_forecast numeric(10, 4) check (rain_forecast is null or rain_forecast >= 0),
  temperature_c numeric(6, 2),
  temperature_forecast numeric(6, 2),
  ingestion_id bigint references public.ingestion_batches(ingestion_id)
);

create table if not exists public.event_observations (
  observed_at timestamptz primary key references public.time_slots(observed_at),
  event_intensity numeric(10, 4) not null check (event_intensity >= 0),
  event_type text,
  event_name text,
  ingestion_id bigint references public.ingestion_batches(ingestion_id)
);

create table if not exists public.data_quality_issues (
  issue_id bigint generated always as identity primary key,
  ingestion_id bigint references public.ingestion_batches(ingestion_id),
  table_name text not null,
  record_key text,
  issue_type text not null,
  severity text not null check (severity in ('info', 'warning', 'error')),
  details text,
  detected_at timestamptz not null default now(),
  resolved_at timestamptz
);

create index if not exists idx_stations_corridor on public.stations(corridor_id);
create index if not exists idx_demand_observed_at on public.demand_observations(observed_at);
create index if not exists idx_demand_station_date on public.demand_observations(station_id, observed_at);
create index if not exists idx_quality_ingestion on public.data_quality_issues(ingestion_id);
