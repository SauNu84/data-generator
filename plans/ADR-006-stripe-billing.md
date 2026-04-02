# ADR-006: Stripe Billing Integration

- **Status**: Accepted
- **Date**: 2026-04-02
- **Deciders**: Enterprise Architect, CTO
- **Issue**: SAU-106 (parent: SAU-105)

---

## Context

Phase 2 monetises the platform with a Free → Pro → Enterprise tier model:

| Tier       | Price        | Limits                                              |
|------------|--------------|-----------------------------------------------------|
| free       | $0           | 10 generations/month, CSV only, no API access       |
| pro        | $49/month    | Unlimited generations, API access, history, templates, PII masking |
| enterprise | Custom       | Multi-table, DB connectors, SSO, SOC2               |

This ADR defines:
1. Stripe Checkout + Customer Portal integration
2. Webhook event handling and idempotency
3. Subscription state machine
4. Free → Paid upgrade flow
5. Enforcement of generation quotas

**Constraints:**
- FastAPI + PostgreSQL stack
- Stripe is the only payment processor evaluated (market standard, strong Python SDK, well-documented webhook patterns)
- Webhook delivery is at-least-once; idempotency is required
- Subscription state must be queryable without hitting Stripe API on every request

---

## Subscription State Machine

```
         ┌─────────────┐
         │    free      │ ◄──────────────────────┐
         └──────┬───────┘                        │
                │ checkout.session.completed      │ subscription.deleted
                ▼                                 │
         ┌─────────────┐                          │
         │  pro_active  │ ──── invoice.payment_failed ──► ┌──────────────┐
         └──────┬───────┘                                  │ pro_past_due │
                │                                          └──────┬───────┘
                │ customer.subscription.updated                   │ grace period expires
                │ (cancel_at_period_end=true)                     │ (after 7 days)
                ▼                                                  │
         ┌──────────────────┐                                      │
         │ pro_cancel_sched │ ─── period end ──────────────────────┘
         └──────────────────┘
```

**States stored in `subscriptions.status`:**
- `free` — no Stripe subscription
- `pro_active` — subscription current, paid
- `pro_past_due` — payment failed, 7-day grace period (full access)
- `pro_cancel_scheduled` — cancels at period end (full access until then)
- `cancelled` — subscription ended, downgraded to free limits

---

## Stripe Checkout Flow

### Upgrade: Free → Pro

```
1. User clicks "Upgrade to Pro" in dashboard

2. POST /billing/checkout
   Auth: JWT
   → Create Stripe Customer (or reuse existing stripe_customer_id)
   → Create Checkout Session:
       stripe.checkout.sessions.create({
         customer: stripe_customer_id,
         mode: "subscription",
         line_items: [{ price: STRIPE_PRO_PRICE_ID, quantity: 1 }],
         success_url: "{APP_URL}/dashboard?upgrade=success",
         cancel_url: "{APP_URL}/pricing",
         metadata: { user_id: str(user.id) }
       })
   → Return: { checkout_url }

3. Browser redirects to Stripe-hosted checkout page
4. User enters card → Stripe processes payment
5. Stripe fires webhook: checkout.session.completed
6. Our webhook handler: set user.tier = 'pro', create subscription record
7. User redirected to success_url → dashboard shows Pro features
```

### Manage Subscription (Cancel, Update)

```
POST /billing/portal
  Auth: JWT
  → stripe.billing_portal.sessions.create({
      customer: stripe_customer_id,
      return_url: "{APP_URL}/dashboard"
    })
  → Return: { portal_url }
  → Browser redirects to Stripe Customer Portal
    (Stripe handles cancel, plan change, card update)
```

---

## Webhook Handler Design

### Endpoint

```
POST /webhooks/stripe
  Auth: Stripe-Signature header (HMAC validation — not JWT)
  → stripe.webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
  → Idempotency check: SELECT FROM webhook_events WHERE stripe_event_id = ...
  → If already processed: return 200 (Stripe retries on non-2xx)
  → Dispatch to handler by event type
  → INSERT webhook_events (stripe_event_id, type, processed_at)
  → Return 200 immediately (Stripe timeout = 30s; async processing via Celery if needed)
```

### Events Handled

| Stripe Event | Action |
|---|---|
| `checkout.session.completed` | Create/update subscription, set `user.tier = 'pro'`, send welcome email |
| `invoice.payment_succeeded` | Update `subscription.current_period_end`, set status `pro_active` |
| `invoice.payment_failed` | Set `subscription.status = 'pro_past_due'`, send payment failure email |
| `customer.subscription.updated` | Sync `cancel_at_period_end` → set `pro_cancel_scheduled` |
| `customer.subscription.deleted` | Set `subscription.status = 'cancelled'`, set `user.tier = 'free'` |

### Idempotency Pattern

```python
@router.post("/webhooks/stripe")
async def stripe_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    payload = await request.body()
    sig = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(payload, sig, settings.STRIPE_WEBHOOK_SECRET)
    except stripe.error.SignatureVerificationError:
        raise HTTPException(400, "Invalid signature")

    # Idempotency: skip already-processed events
    existing = await db.execute(
        select(WebhookEvent).where(WebhookEvent.stripe_event_id == event["id"])
    )
    if existing.scalar_one_or_none():
        return {"status": "already_processed"}

    # Handle event
    await handle_stripe_event(event, db)

    # Record processed event
    db.add(WebhookEvent(stripe_event_id=event["id"], event_type=event["type"]))
    await db.commit()
    return {"status": "ok"}
```

---

## Generation Quota Enforcement

Free tier limit: 10 generations/month. Checked at job creation time.

```python
async def check_generation_quota(user: User, db: AsyncSession):
    if user.tier != "free":
        return  # Pro/Enterprise: unlimited

    month_start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    count = await db.scalar(
        select(func.count(GenerationJob.id))
        .where(GenerationJob.user_id == user.id)
        .where(GenerationJob.created_at >= month_start)
        .where(GenerationJob.status != "cancelled")
    )
    if count >= 10:
        raise HTTPException(
            402,
            detail={
                "error": "generation_quota_exceeded",
                "message": "Free tier limit: 10 generations/month. Upgrade to Pro for unlimited.",
                "upgrade_url": "/billing/checkout"
            }
        )
```

---

## Environment Variables Required

```bash
STRIPE_SECRET_KEY=sk_live_...          # or sk_test_... for dev
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_PRO_PRICE_ID=price_...
STRIPE_TEST_CLOCK_ID=...               # optional, for testing time-based events
```

### Local Development

Use Stripe CLI to forward webhooks to local server:
```bash
stripe listen --forward-to localhost:8000/webhooks/stripe
```

---

## Consequences

**Positive:**
- Stripe Customer Portal handles all billing self-service — zero custom UI needed for cancel/update
- Idempotent webhook handler survives Stripe retry storms
- Subscription state in PostgreSQL = no Stripe API call on every request
- Quota enforcement is a single DB count query — fast and accurate
- `pro_past_due` grace period retains paying customers during card failures

**Negative / Trade-offs:**
- Stripe is the sole billing processor (acceptable at Phase 2; revisit for Enterprise/international)
- Webhook signature validation requires raw request body before JSON parsing (must read raw bytes)
- `webhook_events` table grows unbounded — add a 90-day retention cleanup job (Phase 2 housekeeping)

---

## Dependencies

- `stripe` (Python SDK) — Stripe API client
- No additional infrastructure; uses existing PostgreSQL

---

## Revisit Trigger

Revisit if: Enterprise customers require invoice billing, purchase orders, or non-USD currencies. Trigger: first Enterprise RFP. Also revisit if Stripe pricing changes significantly vs. Paddle/LemonSqueezy alternatives.
