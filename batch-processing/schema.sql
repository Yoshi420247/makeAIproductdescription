-- Supabase schema for Product Description Generator
-- Run this in the Supabase SQL Editor (https://supabase.com/dashboard/project/iezzvdftbcboychqlaav/sql)

-- Tracks each generation run (realtime, realtime-live, batch)
create table if not exists batch_runs (
    id uuid primary key default gen_random_uuid(),
    run_id text unique not null,
    model_provider text not null,
    model_name text not null,
    content_source text not null default 'product_data',
    product_filter text,
    total_products integer not null default 0,
    succeeded integer not null default 0,
    failed integer not null default 0,
    status text not null default 'generating',
    created_at timestamptz not null default now()
);

-- Individual product descriptions with before/after tracking
create table if not exists product_descriptions (
    id uuid primary key default gen_random_uuid(),
    run_id text not null references batch_runs(run_id),
    shopify_product_id bigint not null,
    product_title text not null,
    body_html_before text,
    body_html_after text not null,
    word_count integer not null default 0,
    status text not null default 'generated',
    error_message text,
    applied_at timestamptz,
    created_at timestamptz not null default now()
);

-- Indexes for common queries
create index if not exists idx_descriptions_run_id on product_descriptions(run_id);
create index if not exists idx_descriptions_shopify_id on product_descriptions(shopify_product_id);
create index if not exists idx_descriptions_status on product_descriptions(status);
create index if not exists idx_batch_runs_status on batch_runs(status);
create index if not exists idx_batch_runs_created on batch_runs(created_at desc);

-- Row Level Security (allow service key full access)
alter table batch_runs enable row level security;
alter table product_descriptions enable row level security;

create policy "Service key full access on batch_runs"
    on batch_runs for all
    using (true)
    with check (true);

create policy "Service key full access on product_descriptions"
    on product_descriptions for all
    using (true)
    with check (true);
