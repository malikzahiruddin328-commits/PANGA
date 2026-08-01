// POST /webhooks-stripe — the only source of truth for subscription
// lifecycle. No customer-facing auth here (Stripe calls this directly);
// the signature check IS the auth. Every event is logged to
// webhook_events first, keyed by Stripe's own event id, so a retried
// delivery (Stripe retries on any non-2xx or timeout) is a no-op instead
// of double-applying an update.

import Stripe from "npm:stripe@16";
import { serviceClient } from "../_shared/supabase-client.ts";
import { json, errorResponse } from "../_shared/responses.ts";

const stripe = new Stripe(Deno.env.get("STRIPE_SECRET_KEY")!, { apiVersion: "2024-06-20" });
const WEBHOOK_SECRET = Deno.env.get("STRIPE_WEBHOOK_SECRET")!;

Deno.serve(async (req) => {
  if (req.method !== "POST") return errorResponse("Method not allowed", 405);

  const signature = req.headers.get("stripe-signature");
  if (!signature) return errorResponse("Missing stripe-signature", 400);

  const rawBody = await req.text();
  let event: Stripe.Event;
  try {
    event = await stripe.webhooks.constructEventAsync(rawBody, signature, WEBHOOK_SECRET);
  } catch (e) {
    return errorResponse(`Signature verification failed: ${e}`, 400);
  }

  const db = serviceClient();

  // Idempotency: insert-or-ignore on the event id. If it's already there,
  // this is a Stripe retry of an event we already applied — ack and stop.
  const { error: logErr } = await db
    .from("webhook_events")
    .insert({ id: event.id, type: event.type, payload: event as unknown as Record<string, unknown> });
  if (logErr) {
    if (logErr.code === "23505") return json({ received: true, duplicate: true });
    return errorResponse(logErr.message, 500);
  }

  try {
    switch (event.type) {
      case "checkout.session.completed": {
        const session = event.data.object as Stripe.Checkout.Session;
        const customerId = session.client_reference_id;
        if (customerId && session.customer) {
          await db
            .from("customers")
            .update({ stripe_customer_id: session.customer as string })
            .eq("id", customerId);
        }
        break;
      }

      case "customer.subscription.created":
      case "customer.subscription.updated":
      case "customer.subscription.deleted": {
        const sub = event.data.object as Stripe.Subscription;
        const { data: customer } = await db
          .from("customers")
          .select("id")
          .eq("stripe_customer_id", sub.customer as string)
          .maybeSingle();
        if (!customer) break; // checkout.session.completed hasn't landed yet — a later retry/update will reconcile
        await db.from("subscriptions").upsert(
          {
            customer_id: customer.id,
            stripe_subscription_id: sub.id,
            status: sub.status,
            current_period_end: new Date(sub.current_period_end * 1000).toISOString(),
            updated_at: new Date().toISOString(),
          },
          { onConflict: "customer_id" },
        );
        break;
      }

      case "invoice.paid":
      case "invoice.payment_failed": {
        // Stripe also fires customer.subscription.updated around these
        // events with the authoritative status/period — nothing
        // additional to do here beyond what's already logged for
        // debugging/support lookup via webhook_events.
        break;
      }

      default:
        break; // logged in webhook_events even if unhandled, for later inspection
    }
  } catch (e) {
    // Deliberately still return 200 below is wrong here — a processing
    // failure should make Stripe retry. But webhook_events already has
    // the row, so undo that so the retry doesn't get swallowed as a dup.
    await db.from("webhook_events").delete().eq("id", event.id);
    return errorResponse(String(e), 500);
  }

  return json({ received: true });
});
