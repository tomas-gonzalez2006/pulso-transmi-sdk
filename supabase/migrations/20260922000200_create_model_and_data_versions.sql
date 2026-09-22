-- Versionado auditable de dataset y modelo promovido.
create table if not exists public.dataset_versions (
  dataset_version text primary key,
  created_at timestamptz not null default now(),
  api_url text not null,
  observations_sha256 text not null,
  context_sha256 text not null,
  stations_sha256 text,
  metadata_sha256 text,
  cutoff_at timestamptz,
  rows_observations integer not null default 0,
  rows_context integer not null default 0,
  metadata jsonb not null default '{}'::jsonb
);

create table if not exists public.model_versions (
  model_version text primary key,
  dataset_version text not null references public.dataset_versions(dataset_version),
  created_at timestamptz not null default now(),
  algorithm text not null,
  artifact_sha256 text not null,
  artifact_path text not null,
  git_commit text,
  status text not null default 'candidate' check (status in ('candidate', 'champion', 'retired')),
  metrics jsonb not null default '{}'::jsonb,
  feature_schema jsonb not null default '[]'::jsonb
);

create index if not exists idx_model_versions_status on public.model_versions(status, created_at desc);
alter table public.dataset_versions enable row level security;
alter table public.model_versions enable row level security;
drop policy if exists dashboard_read_dataset_versions on public.dataset_versions;
create policy dashboard_read_dataset_versions on public.dataset_versions for select using (true);
drop policy if exists dashboard_read_model_versions on public.model_versions;
create policy dashboard_read_model_versions on public.model_versions for select using (true);

-- Bucket privado para conservar el artefacto exacto que corresponde a cada versión.
insert into storage.buckets (id, name, public)
values ('model-artifacts', 'model-artifacts', false)
on conflict (id) do nothing;
