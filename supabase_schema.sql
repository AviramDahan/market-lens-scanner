create extension if not exists pgcrypto;

create table if not exists public.profiles (
    id uuid primary key references auth.users(id) on delete cascade,
    display_name text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.global_setups (
    id uuid primary key default gen_random_uuid(),
    fingerprint text not null unique,
    ticker text not null,
    setup_type text not null,
    analysis_period text not null,
    status text not null default 'OPEN',
    score double precision not null default 0,
    current_price double precision not null default 0,
    found_price double precision not null default 0,
    stop_loss double precision not null default 0,
    target_1 double precision not null default 0,
    target_2 double precision not null default 0,
    risk_reward double precision not null default 0,
    chart_url text,
    result_json jsonb not null,
    found_by uuid references auth.users(id) on delete set null,
    found_by_name text,
    scan_count integer not null default 1,
    created_at timestamptz not null default now(),
    last_seen_at timestamptz not null default now(),
    status_checked_at timestamptz,
    hit_at timestamptz
);

create table if not exists public.user_saved_setups (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade,
    global_setup_id uuid references public.global_setups(id) on delete set null,
    ticker text not null,
    setup_type text not null,
    analysis_period text not null,
    status text not null default 'OPEN',
    score double precision not null default 0,
    saved_price double precision not null default 0,
    current_price double precision not null default 0,
    stop_loss double precision not null default 0,
    target_1 double precision not null default 0,
    target_2 double precision not null default 0,
    risk_reward double precision not null default 0,
    chart_url text,
    result_json jsonb not null,
    notes text,
    created_at timestamptz not null default now(),
    status_checked_at timestamptz,
    hit_at timestamptz,
    unique (user_id, global_setup_id)
);

create index if not exists idx_global_setups_created on public.global_setups(created_at desc);
create index if not exists idx_global_setups_status on public.global_setups(status);
create index if not exists idx_global_setups_ticker on public.global_setups(ticker);
create index if not exists idx_user_saved_setups_user_created on public.user_saved_setups(user_id, created_at desc);
create index if not exists idx_user_saved_setups_status on public.user_saved_setups(status);

alter table public.profiles enable row level security;
alter table public.global_setups enable row level security;
alter table public.user_saved_setups enable row level security;

drop policy if exists "profiles_select_own" on public.profiles;
create policy "profiles_select_own"
on public.profiles for select
to authenticated
using (id = auth.uid());

drop policy if exists "profiles_insert_own" on public.profiles;
create policy "profiles_insert_own"
on public.profiles for insert
to authenticated
with check (id = auth.uid());

drop policy if exists "profiles_update_own" on public.profiles;
create policy "profiles_update_own"
on public.profiles for update
to authenticated
using (id = auth.uid())
with check (id = auth.uid());

drop policy if exists "global_setups_read_all" on public.global_setups;
create policy "global_setups_read_all"
on public.global_setups for select
to anon, authenticated
using (true);

drop policy if exists "user_saved_select_own" on public.user_saved_setups;
create policy "user_saved_select_own"
on public.user_saved_setups for select
to authenticated
using (user_id = auth.uid());

drop policy if exists "user_saved_insert_own" on public.user_saved_setups;
create policy "user_saved_insert_own"
on public.user_saved_setups for insert
to authenticated
with check (user_id = auth.uid());

drop policy if exists "user_saved_update_own" on public.user_saved_setups;
create policy "user_saved_update_own"
on public.user_saved_setups for update
to authenticated
using (user_id = auth.uid())
with check (user_id = auth.uid());

drop policy if exists "user_saved_delete_own" on public.user_saved_setups;
create policy "user_saved_delete_own"
on public.user_saved_setups for delete
to authenticated
using (user_id = auth.uid());
