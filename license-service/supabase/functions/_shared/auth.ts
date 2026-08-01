// Resolves the calling customer from the Supabase Auth JWT in the
// Authorization header. Every customer-facing endpoint (not the Stripe
// webhook, which verifies its own signature instead) calls this first.

import { createClient } from "jsr:@supabase/supabase-js@2";

export interface AuthedCustomer {
  customerId: string;
  email: string;
}

export async function requireCustomer(req: Request): Promise<AuthedCustomer> {
  const authHeader = req.headers.get("Authorization");
  if (!authHeader?.startsWith("Bearer ")) {
    throw new AuthError("Missing bearer token");
  }
  const token = authHeader.slice("Bearer ".length);

  // A plain anon-key client is enough here — we're only using it to
  // validate the JWT and recover the user it belongs to, not to bypass RLS.
  const anon = createClient(
    Deno.env.get("SUPABASE_URL")!,
    Deno.env.get("SUPABASE_ANON_KEY")!,
  );
  const { data, error } = await anon.auth.getUser(token);
  if (error || !data.user) {
    throw new AuthError("Invalid or expired session");
  }
  return { customerId: data.user.id, email: data.user.email ?? "" };
}

export class AuthError extends Error {}
