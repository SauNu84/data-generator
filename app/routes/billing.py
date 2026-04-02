"""
Stripe billing routes:
  POST /api/billing/checkout      — create Stripe Checkout session (Pro upgrade)
  GET  /api/billing/usage         — current usage summary for authenticated user
  POST /api/webhooks/stripe       — Stripe webhook handler
"""

import stripe
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.deps import get_current_user
from app.models import Subscription, UsageEvent, User
from app.schemas import CheckoutSessionResponse, UsageSummaryResponse

stripe.api_key = settings.stripe_secret_key

router = APIRouter(tags=["billing"])


# ─── Checkout Session ─────────────────────────────────────────────────────────

@router.post("/api/billing/checkout", response_model=CheckoutSessionResponse)
async def create_checkout_session(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if user.tier in ("pro", "enterprise"):
        raise HTTPException(status_code=400, detail="Already on Pro or higher.")

    # Create or retrieve Stripe customer
    if user.stripe_customer_id:
        customer_id = user.stripe_customer_id
    else:
        customer = stripe.Customer.create(email=user.email)
        customer_id = customer.id
        user.stripe_customer_id = customer_id
        await db.commit()

    session = stripe.checkout.Session.create(
        customer=customer_id,
        mode="subscription",
        line_items=[{"price": settings.stripe_pro_price_id, "quantity": 1}],
        success_url=f"{settings.app_base_url}/dashboard?upgraded=1",
        cancel_url=f"{settings.app_base_url}/dashboard?upgrade_cancelled=1",
        metadata={"user_id": str(user.id)},
    )

    return CheckoutSessionResponse(checkout_url=session.url)


# ─── Usage Summary ────────────────────────────────────────────────────────────

@router.get("/api/billing/usage", response_model=UsageSummaryResponse)
async def get_usage_summary(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    used = await db.scalar(
        select(func.count(UsageEvent.id)).where(
            UsageEvent.user_id == user.id,
            UsageEvent.event_type == "generation",
            UsageEvent.created_at >= month_start,
        )
    )

    limit = None if user.tier != "free" else settings.free_tier_monthly_generations
    return UsageSummaryResponse(
        tier=user.tier,
        monthly_generations_used=used or 0,
        monthly_generations_limit=limit,
    )


# ─── Stripe Webhook ───────────────────────────────────────────────────────────

@router.post("/api/webhooks/stripe", status_code=200)
async def stripe_webhook(
    request: Request,
    stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
    db: AsyncSession = Depends(get_db),
):
    payload = await request.body()

    try:
        event = stripe.Webhook.construct_event(
            payload, stripe_signature, settings.stripe_webhook_secret
        )
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid webhook signature.")
    except Exception:
        raise HTTPException(status_code=400, detail="Malformed webhook payload.")

    event_type = event["type"]

    if event_type == "checkout.session.completed":
        await _handle_checkout_completed(event["data"]["object"], db)

    elif event_type in ("customer.subscription.updated", "customer.subscription.deleted"):
        await _handle_subscription_update(event["data"]["object"], db)

    elif event_type == "payment_intent.succeeded":
        # Informational only for M1 — subscription lifecycle handled above
        pass

    return {"received": True}


# ─── Webhook Helpers ──────────────────────────────────────────────────────────

async def _handle_checkout_completed(session: dict, db: AsyncSession) -> None:
    customer_id = session.get("customer")
    subscription_id = session.get("subscription")
    user_id = (session.get("metadata") or {}).get("user_id")

    if not customer_id or not subscription_id:
        return

    # Resolve user
    user: User | None = None
    if user_id:
        import uuid as _uuid
        user = await db.get(User, _uuid.UUID(user_id))
    if not user:
        user = await db.scalar(select(User).where(User.stripe_customer_id == customer_id))
    if not user:
        return

    # Fetch subscription details from Stripe
    sub = stripe.Subscription.retrieve(subscription_id)
    price_id = sub["items"]["data"][0]["price"]["id"] if sub["items"]["data"] else ""

    # Upsert Subscription row
    existing = await db.scalar(
        select(Subscription).where(Subscription.stripe_subscription_id == subscription_id)
    )
    if existing:
        existing.status = sub["status"]
        existing.current_period_start = datetime.fromtimestamp(sub["current_period_start"], tz=timezone.utc)
        existing.current_period_end = datetime.fromtimestamp(sub["current_period_end"], tz=timezone.utc)
        existing.updated_at = datetime.now(timezone.utc)
    else:
        db.add(Subscription(
            user_id=user.id,
            stripe_subscription_id=subscription_id,
            stripe_price_id=price_id,
            status=sub["status"],
            current_period_start=datetime.fromtimestamp(sub["current_period_start"], tz=timezone.utc),
            current_period_end=datetime.fromtimestamp(sub["current_period_end"], tz=timezone.utc),
        ))

    # Upgrade user tier
    if sub["status"] in ("active", "trialing"):
        user.tier = "pro"

    await db.commit()


async def _handle_subscription_update(sub: dict, db: AsyncSession) -> None:
    subscription_id = sub.get("id")
    customer_id = sub.get("customer")
    if not subscription_id:
        return

    user = await db.scalar(select(User).where(User.stripe_customer_id == customer_id))
    existing = await db.scalar(
        select(Subscription).where(Subscription.stripe_subscription_id == subscription_id)
    )

    status = sub.get("status", "canceled")

    if existing:
        existing.status = status
        existing.updated_at = datetime.now(timezone.utc)
        if sub.get("current_period_start"):
            existing.current_period_start = datetime.fromtimestamp(sub["current_period_start"], tz=timezone.utc)
        if sub.get("current_period_end"):
            existing.current_period_end = datetime.fromtimestamp(sub["current_period_end"], tz=timezone.utc)

    # Downgrade user tier if subscription is cancelled
    if user and status in ("canceled", "unpaid", "past_due"):
        # Only downgrade if no other active subscriptions
        active_count = await db.scalar(
            select(func.count(Subscription.id)).where(
                Subscription.user_id == user.id,
                Subscription.status.in_(["active", "trialing"]),
                Subscription.stripe_subscription_id != subscription_id,
            )
        )
        if (active_count or 0) == 0:
            user.tier = "free"

    await db.commit()
