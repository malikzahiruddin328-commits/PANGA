// POST /billing-portal-session — a Stripe customer portal link so the
// customer can update card details, view invoices, or cancel, without us
// building any of that ourselves.

import Stripe from "npm:stripe@16";
import { serviceClient } from "../_shared/supabase-client.ts";
import { requireCustomer, AuthError } from "../_shared/auth.ts";
import { json, errorResponse, corsHeaders } from "../_shared/responses.ts";

const stripe = new Stripe(Deno.env.get("STRIPE_SECRET_KEY")!, { apiVersion: "2024-06-20" });

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response(null, { headers: corsHeaders });
  if (req.method !== "POST") return errorResponse("Method not allowed", 405);

  try {
    const { customerId } = await requireCustomer(req);
    const body = await req.json().catch(() => ({}));
    const returnUrl = body.return_url;
    if (!returnUrl) return errorResponse("return_url required", 400);

    const db = serviceClient();
    const { data: customer, error: custErr } = await db
      .from("customers")
      .select("stripe_customer_id")
      .eq("id", customerId)
      .single();
    if (custErr) return errorResponse(custErr.message, 500);
    if (!customer.stripe_customer_id) {
      return errorResponse("No billing account yet — start a subscription first", 409);
    }

    const session = await stripe.billingPortal.sessions.create({
      customer: customer.stripe_customer_id,
      return_url: returnUrl,
    });

    return json({ portal_url: session.url });
  } catch (e) {
    if (e instanceof AuthError) return errorResponse(e.message, 401);
    return errorResponse(String(e), 500);
  }
});
