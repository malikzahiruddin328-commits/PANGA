// POST /billing-create-checkout-session — starts Stripe Checkout for the
// paid subscription (called when the trial is ending or the customer
// upgrades early). Reuses stripe_customer_id if we already have one so
// repeat checkouts don't create duplicate Stripe customers.

import Stripe from "npm:stripe@16";
import { serviceClient } from "../_shared/supabase-client.ts";
import { requireCustomer, AuthError } from "../_shared/auth.ts";
import { json, errorResponse, corsHeaders } from "../_shared/responses.ts";

const stripe = new Stripe(Deno.env.get("STRIPE_SECRET_KEY")!, { apiVersion: "2024-06-20" });
const PRICE_ID = Deno.env.get("STRIPE_PRICE_ID")!;

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response(null, { headers: corsHeaders });
  if (req.method !== "POST") return errorResponse("Method not allowed", 405);

  try {
    const { customerId, email } = await requireCustomer(req);
    const body = await req.json().catch(() => ({}));
    const successUrl = body.success_url;
    const cancelUrl = body.cancel_url;
    if (!successUrl || !cancelUrl) return errorResponse("success_url and cancel_url required", 400);

    const db = serviceClient();
    const { data: customer, error: custErr } = await db
      .from("customers")
      .select("stripe_customer_id")
      .eq("id", customerId)
      .single();
    if (custErr) return errorResponse(custErr.message, 500);

    const session = await stripe.checkout.sessions.create({
      mode: "subscription",
      customer: customer.stripe_customer_id ?? undefined,
      customer_email: customer.stripe_customer_id ? undefined : email,
      line_items: [{ price: PRICE_ID, quantity: 1 }],
      success_url: successUrl,
      cancel_url: cancelUrl,
      // Carried through to the webhook so checkout.session.completed can
      // link the Stripe customer back to our customer row without relying
      // on email matching (emails can differ, e.g. a work alias at Stripe).
      client_reference_id: customerId,
    });

    return json({ checkout_url: session.url });
  } catch (e) {
    if (e instanceof AuthError) return errorResponse(e.message, 401);
    return errorResponse(String(e), 500);
  }
});
