-- Ejecutar en Supabase > SQL Editor.
-- No borra datos: crea la tabla si no existe y agrega solo columnas faltantes.

create extension if not exists pgcrypto;

create table if not exists public.liq_2026 (
    id uuid primary key default gen_random_uuid(),
    escritura integer not null,
    correo text,
    gobernacion text,
    nir text,
    notificacion text,
    pago text,
    estado_ctl text,
    devolucion text,
    escritura_str text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    observaciones text,
    fecha_proceso timestamptz,
    responsable text,

    -- Columnas del informe Informe Not 38 (Liq)
    cert text,
    copias text,
    fecha_liq date,
    benef date,
    fecha_pago date,
    radicado text,
    ingreso text
);

-- Mantiene una tabla ya existente y completa cualquier columna que le falte.
alter table public.liq_2026 add column if not exists id uuid default gen_random_uuid();
alter table public.liq_2026 add column if not exists escritura integer;
alter table public.liq_2026 add column if not exists correo text;
alter table public.liq_2026 add column if not exists gobernacion text;
alter table public.liq_2026 add column if not exists nir text;
alter table public.liq_2026 add column if not exists notificacion text;
alter table public.liq_2026 add column if not exists pago text;
alter table public.liq_2026 add column if not exists estado_ctl text;
alter table public.liq_2026 add column if not exists devolucion text;
alter table public.liq_2026 add column if not exists escritura_str text;
alter table public.liq_2026 add column if not exists created_at timestamptz default now();
alter table public.liq_2026 add column if not exists updated_at timestamptz default now();
alter table public.liq_2026 add column if not exists observaciones text;
alter table public.liq_2026 add column if not exists fecha_proceso timestamptz;
alter table public.liq_2026 add column if not exists responsable text;
alter table public.liq_2026 add column if not exists cert text;
alter table public.liq_2026 add column if not exists copias text;
alter table public.liq_2026 add column if not exists fecha_liq date;
alter table public.liq_2026 add column if not exists benef date;
alter table public.liq_2026 add column if not exists fecha_pago date;
alter table public.liq_2026 add column if not exists radicado text;
alter table public.liq_2026 add column if not exists ingreso text;

create index if not exists idx_liq_2026_escritura on public.liq_2026 (escritura);
create index if not exists idx_liq_2026_fecha_proceso on public.liq_2026 (fecha_proceso);
create index if not exists idx_liq_2026_estado_ctl on public.liq_2026 (estado_ctl);

-- Verificación opcional después de ejecutar el script:
-- select column_name, data_type
-- from information_schema.columns
-- where table_schema = 'public' and table_name = 'liq_2026'
-- order by ordinal_position;
