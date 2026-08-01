// Deriving "is this customer currently entitled to use the app" from the
// trial/subscription rows. This function only ever runs when the client
// successfully reached the server — so its answer is authoritative
// (matches docs/licensing-scope.md's "state 4: actually expired, confirmed
// via a real successful check-in"). The offline 3-day grace period is a
// purely client-side concept (see src/licensing/local_state.py) — the
// server has no notion of "can't verify", only "verified" or "expired".

export interface EntitlementResult {
  status: "verified" | "expired";
  reason?: "trial" | "subscription";
  expiresAt?: string;
}

export function deriveEntitlement(
  trial: { ends_at: string } | null,
  subscription: { status: string; current_period_end: string } | null,
): EntitlementResult {
  const now = new Date();

  if (subscription) {
    // Stripe's own dunning (past_due retries) is the billing-side grace
    // period; once Stripe marks it canceled/unpaid, or the paid period has
    // actually elapsed, the customer is expired here.
    const periodEnd = new Date(subscription.current_period_end);
    const stripeConsidersActive =
      subscription.status === "trialing" ||
      subscription.status === "active" ||
      subscription.status === "past_due";
    if (stripeConsidersActive && now <= periodEnd) {
      return { status: "verified", expiresAt: subscription.current_period_end };
    }
    return { status: "expired", reason: "subscription", expiresAt: subscription.current_period_end };
  }

  if (trial) {
    const endsAt = new Date(trial.ends_at);
    if (now <= endsAt) {
      return { status: "verified", expiresAt: trial.ends_at };
    }
    return { status: "expired", reason: "trial", expiresAt: trial.ends_at };
  }

  // No trial and no subscription row at all — treat as an expired trial
  // (shouldn't normally happen once trial/start is called on first launch).
  return { status: "expired", reason: "trial" };
}
