# Panga license service

Serverless licensing/subscription backend — see
`../docs/licensing-scope.md` for the product design this implements.
Supabase (Postgres + Edge Functions + built-in email-OTP auth), chosen
over rolling auth/admin tooling by hand (see that doc's "confirmed with
Zahir" note).

## One-time setup

1. Create a Supabase project (free tier is enough at this scale).
2. In the Supabase dashboard → Authentication → Providers, enable **Email**
   with OTP (magic-code) sign-in, disable password sign-in — the app's
   onboarding is "enter your email to continue," not a password form.
3. Create a Stripe product + a 1-year recurring price; note the price id.
4. Create a Stripe webhook endpoint pointing at
   `https://<project-ref>.supabase.co/functions/v1/webhooks-stripe`,
   subscribed to: `checkout.session.completed`,
   `customer.subscription.created`, `customer.subscription.updated`,
   `customer.subscription.deleted`, `invoice.paid`,
   `invoice.payment_failed`. Note the signing secret.
5. Set secrets for the Edge Functions runtime (`supabase secrets set`):
   - `STRIPE_SECRET_KEY`
   - `STRIPE_WEBHOOK_SECRET`
   - `STRIPE_PRICE_ID`
   - `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`
     (the last is available from the project settings; the Edge Functions
     runtime also injects `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY`
     automatically for you in most cases — set explicitly if not).

## Deploy

```bash
supabase link --project-ref <project-ref>
supabase db push                       # applies supabase/migrations/
supabase functions deploy trial-start
supabase functions deploy license-status
supabase functions deploy device-activate
supabase functions deploy device-release
supabase functions deploy billing-create-checkout-session
supabase functions deploy billing-portal-session
supabase functions deploy webhooks-stripe --no-verify-jwt
```

## Manual admin review (lost/stolen device)

Deliberately not a built endpoint at launch (see licensing-scope.md).
Zahir reviews the request, then in Supabase Studio's table editor:

1. Open `devices`, find the customer's active row (`released_at is null`).
2. Set `released_at` to now.
3. Open `device_transfers`, insert a row: `transfer_type = 'admin_override'`,
   `actor` = Zahir's identifier, `reason` = free text on what was verified.

This bypasses RLS because it's done while authenticated as the project
owner in Studio, not through the anon-key API — the customer-facing
Edge Functions never expose this path.

## Endpoints

| Function | Auth | Purpose |
|---|---|---|
| `trial-start` | customer session | Idempotent trial creation, 15 days from first call |
| `license-status` | customer session | Check-in: returns `verified` or `expired` (`reason: trial\|subscription`) |
| `device-activate` | customer session | Bind this device as the license's one active device |
| `device-release` | customer session (interactive or the uninstaller, `via` field distinguishes them) | Release the active device binding; rate-limited to 1/30 days |
| `billing-create-checkout-session` | customer session | Stripe Checkout for the paid subscription |
| `billing-portal-session` | customer session | Stripe customer portal (manage/cancel) |
| `webhooks-stripe` | Stripe signature | Subscription lifecycle sync |

## Open dependency points (not yet resolved)

- `devices.device_fingerprint` format/generation is native-packaging's
  call once that branch's installed-app shape is final.
- Awaiting confirmation from the native-packaging session on whether the
  uninstaller can reach the locally cached credential *before* the OS
  keyring entry is removed (affects whether `device-release via: "uninstall"`
  can ever actually authenticate).
