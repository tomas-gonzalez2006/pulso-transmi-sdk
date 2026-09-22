create table if not exists public.api_cursors (
  source_name text primary key,
  cursor_value text,
  updated_at timestamptz not null default now()
);

alter table public.api_cursors enable row level security;
drop policy if exists dashboard_read_api_cursors on public.api_cursors;
create policy dashboard_read_api_cursors on public.api_cursors for select using (true);
