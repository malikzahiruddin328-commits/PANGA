// GET/POST /license-status — the check-in call, made on app launch plus a
// daily background check. Every call here reached the server, so its
// answer is authoritative ("verified" or "expired" — never "grace", that's
// a purely local concept, see _shared/entitlement.ts).
//
// Body: { device_fingerprint: string }

import { serviceClient } from "../_shared/supabase-client.ts";
import { requireCustomer, AuthError } from "../_shared/auth.ts";
import { deriveEntitlement } from "../_shared/entitlement.ts";
import { json, errorResponse, corsHeaders } from "../_shared/responses.ts";

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response(null, { headers: corsHeaders });

  try {
    const { customerId } = await requireCustomer(req);
    const body = await req.json().catch(() => ({}));
    const deviceFingerprint = body.device_fingerprint;
    if (!deviceFingerprint) return errorResponse("device_fingerprint required", 400);

    const db = serviceClient();

    const { data: device, error: deviceErr } = await db
      .from("devices")
      .select("id, device_fingerprint")
      .eq("customer_id", customerId)
      .is("released_at", null)
      .maybeSingle();
    if (deviceErr) return errorResponse(deviceErr.message, 500);
    if (!device) {
      return errorResponse("No active device binding for this license — activate first", 409);
    }
    if (device.device_fingerprint !== deviceFingerprint) {
      // A different device holds the active binding. Don't leak details
      // about the other device — just tell this caller it isn't the one.
      return errorResponse("This device is not the active licensed device", 403);
    }

    const [{ data: trial }, { data: subscription }] = await Promise.all([
      db.from("trials").select("ends_at").eq("customer_id", customerId).maybeSingle(),
      db
        .from("subscriptions")
        .select("status, current_period_end")
        .eq("customer_id", customerId)
        .maybeSingle(),
    ]);

    const entitlement = deriveEntitlement(trial ?? null, subscription ?? null);

    // Update last_checkin_at regardless of entitlement outcome — this
    // timestamp is what the client's own grace-period clock resyncs
    // against on the next successful call, whether the license turns out
    // verified or expired.
    await db.from("devices").update({ last_checkin_at: new Date().toISOString() }).eq("id", device.id);

    return json(entitlement);
  } catch (e) {
    if (e instanceof AuthError) return errorResponse(e.message, 401);
    return errorResponse(String(e), 500);
  }
});
