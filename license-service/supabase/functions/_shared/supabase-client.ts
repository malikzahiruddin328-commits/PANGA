// Service-role client used inside every function. Never expose the
// service-role key to the app itself — it bypasses RLS, so it only ever
// lives in the Edge Function runtime's own environment.

import { createClient } from "jsr:@supabase/supabase-js@2";

export function serviceClient() {
  return createClient(
    Deno.env.get("SUPABASE_URL")!,
    Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
  );
}
