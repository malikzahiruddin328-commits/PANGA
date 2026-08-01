-- Panga license service — initial schema.
-- See docs/licensing-scope.md for the product design this implements.

create extension if not exists citext;
create extension if not exists pgcrypto;

-- customers.id IS the Supabase Auth user id (1:1), not a separately
-- generated uuid — Supabase Auth's email-OTP flow already owns identity
-- and email uniqueness, so this avoids a second identity system to keep in
-- sync. `email` is denormalized from auth.users onto this row anyway so
-- Zahir can read it directly in Supabase Studio without joining into the
-- auth schema during a manual support review.
create table customers (
  id                  uuid primary key references auth.users (id) on delete cascade,
  email               citext unique not null,
  stripe_customer_id  text unique,
  created_at          timestamptz not null default now()
);

-- One trial per customer, ever. Server-side start time so reinstalling the
-- app (or signing up again with the same email) never resets the clock.
create table trials (
  id           uuid primary key default gen_random_uuid(),
  customer_id  uuid not null unique references customers (id),
  started_at   timestamptz not null default now(),
  ends_at      timestamptz not null,
  created_at   timestamptz not null default now()
);

-- One row per customer's subscription. `status` mirrors Stripe's own
-- subscription status values verbatim (trialing/active/past_due/canceled/
-- unpaid) rather than inventing a parallel enum that can drift out of sync.
create table subscriptions (
  id                      uuid primary key default gen_random_uuid(),
  customer_id             uuid not null unique references customers (id),
  stripe_subscription_id  text unique,
  status                  text not null,
  current_period_end      timestamptz not null,
  created_at              timestamptz not null default now(),
  updated_at              timestamptz not null default now()
);

-- device_fingerprint format is a placeholder pending native-packaging's
-- installed-app shape (see docs/licensing-scope.md "Explicit dependencies").
create table devices (
  id                  uuid primary key default gen_random_uuid(),
  customer_id         uuid not null references customers (id),
  device_fingerprint  text not null,
  activated_at        timestamptz not null default now(),
  last_checkin_at     timestamptz not null default now(),
  released_at         timestamptz
);

-- Enforces "one active device per license" at the database level, not just
-- in application code — a released device (released_at is not null) frees
-- up the slot for a new activation.
create unique index devices_one_active_per_customer
  on devices (customer_id)
  where released_at is null;

create index devices_customer_id_idx on devices (customer_id);

-- Audit trail for every device release/transfer, and the source of truth
-- for the 30-day self-service transfer rate limit.
create table device_transfers (
  id              uuid primary key default gen_random_uuid(),
  customer_id     uuid not null references customers (id),
  from_device_id  uuid references devices (id),
  to_device_id    uuid references devices (id),
  transfer_type   text not null check (transfer_type in ('self_service', 'admin_override', 'uninstall')),
  actor           text not null,
  reason          text,
  created_at      timestamptz not null default now()
);

create index device_transfers_customer_recent_idx
  on device_transfers (customer_id, created_at desc);

-- Idempotency log for Stripe webhook deliveries (Stripe retries on timeout,
-- so the same event id can arrive more than once).
create table webhook_events (
  id           text primary key,
  type         text not null,
  payload      jsonb not null,
  received_at  timestamptz not null default now()
);

-- Standard Supabase pattern: mirror every new auth.users row into
-- customers automatically, right after Supabase Auth creates it (e.g. on
-- first email-OTP verification). Keeps "a customer exists" and "an
-- authenticated user exists" as one fact instead of two that can drift.
create function handle_new_auth_user()
returns trigger
language plpgsql
security definer set search_path = public
as $$
begin
  insert into public.customers (id, email)
  values (new.id, new.email);
  return new;
end;
$$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute procedure handle_new_auth_user();

-- Row Level Security: all customer-facing access goes through Edge
-- Functions using the service-role key, never the anon key directly, so
-- policies stay deny-by-default. Manual admin review (lost/stolen device
-- release) happens by a human editing rows directly in Supabase Studio
-- while authenticated as the project owner, which bypasses RLS by design —
-- no separate admin endpoint or role needed for that flow at this scale.
alter table customers enable row level security;
alter table trials enable row level security;
alter table subscriptions enable row level security;
alter table devices enable row level security;
alter table device_transfers enable row level security;
alter table webhook_events enable row level security;
