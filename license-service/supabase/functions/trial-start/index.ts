// POST /trial-start — idempotent: if this customer already has a trial
// row, returns it unchanged rather than resetting the clock. Server-side
// start time so reinstalling the app can't grant a fresh trial.

import { serviceClient } from "../_shared/supabase-client.ts";
import { requireCustomer, AuthError } from "../_shared/auth.ts";
import { json, errorResponse, corsHeaders } from "../_shared/responses.ts";

const TRIAL_DAYS = 15;

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response(null, { headers: corsHeaders });
  if (req.method !== "POST") return errorResponse("Method not allowed", 405);

  try {
    const { customerId } = await requireCustomer(req);
    const db = serviceClient();

    const { data: existing, error: fetchErr } = await db
      .from("trials")
      .select("started_at, ends_at")
      .eq("customer_id", customerId)
      .maybeSingle();
    if (fetchErr) return errorResponse(fetchErr.message, 500);
    if (existing) return json({ started_at: existing.started_at, ends_at: existing.ends_at });

    const startedAt = new Date();
    const endsAt = new Date(startedAt.getTime() + TRIAL_DAYS * 24 * 60 * 60 * 1000);

    const { error: insertErr } = await db.from("trials").insert({
      customer_id: customerId,
      started_at: startedAt.toISOString(),
      ends_at: endsAt.toISOString(),
    });
    // Unique violation on customer_id means a concurrent request already
    // created it (e.g. two rapid launches) — treat that as success and
    // return the row that won, rather than surfacing a spurious error.
    if (insertErr && insertErr.code !== "23505") return errorResponse(insertErr.message, 500);

    const { data: row, error: refetchErr } = await db
      .from("trials")
      .select("started_at, ends_at")
      .eq("customer_id", customerId)
      .single();
    if (refetchErr) return errorResponse(refetchErr.message, 500);

    return json({ started_at: row.started_at, ends_at: row.ends_at });
  } catch (e) {
    if (e instanceof AuthError) return errorResponse(e.message, 401);
    return errorResponse(String(e), 500);
  }
});
