// POST /device-release — releases the caller's own active device binding.
// Two callers, same endpoint:
//   - the in-app "Deactivate this device" button (via: "self_service")
//   - native-packaging's uninstaller, headless, best-effort (via: "uninstall")
// Both count against the 30-day transfer rate limit — an uninstall/
// reinstall cycle is the same "free up my slot" action as the button, and
// the limit exists specifically to stop repeated device swapping from
// being used as a sharing workaround (see docs/licensing-scope.md).
// Admin overrides (lost/stolen device) don't go through this endpoint at
// all — Zahir edits the devices/device_transfers rows directly in
// Supabase Studio, which is unaffected by this limit.
//
// Body: { device_fingerprint: string, via?: "self_service" | "uninstall" }

import { serviceClient } from "../_shared/supabase-client.ts";
import { requireCustomer, AuthError } from "../_shared/auth.ts";
import { json, errorResponse, corsHeaders } from "../_shared/responses.ts";

const RATE_LIMIT_DAYS = 30;

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response(null, { headers: corsHeaders });
  if (req.method !== "POST") return errorResponse("Method not allowed", 405);

  try {
    const { customerId } = await requireCustomer(req);
    const body = await req.json().catch(() => ({}));
    const deviceFingerprint = body.device_fingerprint;
    const via: "self_service" | "uninstall" = body.via === "uninstall" ? "uninstall" : "self_service";
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
      // Nothing active to release — idempotent no-op (a retried uninstall
      // call, or a button click after the device was already released).
      return json({ released: true, already_released: true });
    }
    if (device.device_fingerprint !== deviceFingerprint) {
      return errorResponse("This device does not hold the active binding", 403);
    }

    const rateLimitCutoff = new Date(Date.now() - RATE_LIMIT_DAYS * 24 * 60 * 60 * 1000).toISOString();
    const { data: recentTransfer, error: rateErr } = await db
      .from("device_transfers")
      .select("id, created_at")
      .eq("customer_id", customerId)
      .in("transfer_type", ["self_service", "uninstall"])
      .gte("created_at", rateLimitCutoff)
      .order("created_at", { ascending: false })
      .limit(1)
      .maybeSingle();
    if (rateErr) return errorResponse(rateErr.message, 500);
    if (recentTransfer) {
      return errorResponse(
        `A device was already transferred within the last ${RATE_LIMIT_DAYS} days — contact support for an exception`,
        429,
      );
    }

    // Conditional on released_at still being null: guards against two
    // concurrent release calls (e.g. the button clicked twice, or the
    // uninstaller racing an in-app release) both trying to free the same
    // slot and double-writing the transfer log below.
    const { data: updated, error: releaseErr } = await db
      .from("devices")
      .update({ released_at: new Date().toISOString() })
      .eq("id", device.id)
      .is("released_at", null)
      .select("id");
    if (releaseErr) return errorResponse(releaseErr.message, 500);
    if (!updated || updated.length === 0) {
      // Lost the race — another call already released it just now.
      return json({ released: true, already_released: true });
    }

    await db.from("device_transfers").insert({
      customer_id: customerId,
      from_device_id: device.id,
      to_device_id: null,
      transfer_type: via,
      actor: via === "uninstall" ? "uninstaller" : "customer",
    });

    return json({ released: true });
  } catch (e) {
    if (e instanceof AuthError) return errorResponse(e.message, 401);
    return errorResponse(String(e), 500);
  }
});
