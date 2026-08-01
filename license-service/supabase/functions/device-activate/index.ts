// POST /device-activate — binds this device as the license's one active
// device. Rejects if a different device already holds the active binding
// (the customer needs to release it first — self-service or via support).
//
// Body: { device_fingerprint: string }

import { serviceClient } from "../_shared/supabase-client.ts";
import { requireCustomer, AuthError } from "../_shared/auth.ts";
import { json, errorResponse, corsHeaders } from "../_shared/responses.ts";

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response(null, { headers: corsHeaders });
  if (req.method !== "POST") return errorResponse("Method not allowed", 405);

  try {
    const { customerId } = await requireCustomer(req);
    const body = await req.json().catch(() => ({}));
    const deviceFingerprint = body.device_fingerprint;
    if (!deviceFingerprint) return errorResponse("device_fingerprint required", 400);

    const db = serviceClient();

    const { data: active, error: activeErr } = await db
      .from("devices")
      .select("id, device_fingerprint")
      .eq("customer_id", customerId)
      .is("released_at", null)
      .maybeSingle();
    if (activeErr) return errorResponse(activeErr.message, 500);

    if (active) {
      // Already activated on exactly this device — idempotent no-op so a
      // retried activation call (e.g. flaky network on first launch)
      // doesn't error.
      if (active.device_fingerprint === deviceFingerprint) {
        return json({ activated: true, device_id: active.id });
      }
      return errorResponse(
        "This license is already activated on another device — release it first",
        409,
      );
    }

    const { data: created, error: insertErr } = await db
      .from("devices")
      .insert({ customer_id: customerId, device_fingerprint: deviceFingerprint })
      .select("id")
      .single();
    // The partial unique index (customer_id where released_at is null) is
    // the real race guard: if two activation requests land concurrently,
    // one wins and the other gets a unique-violation here rather than both
    // succeeding.
    if (insertErr) {
      if (insertErr.code === "23505") {
        return errorResponse(
          "This license is already activated on another device — release it first",
          409,
        );
      }
      return errorResponse(insertErr.message, 500);
    }

    return json({ activated: true, device_id: created.id });
  } catch (e) {
    if (e instanceof AuthError) return errorResponse(e.message, 401);
    return errorResponse(String(e), 500);
  }
});
