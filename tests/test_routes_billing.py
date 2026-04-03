"""
Integration tests for app/routes/billing.py — SAU-117

Coverage target: ≥85% on app/routes/billing.py

Scenarios:
  POST /api/billing/checkout       — free user gets checkout URL, existing Stripe customer reused,
                                     already-Pro returns 400, requires auth (401)
  GET  /api/billing/usage          — returns tier + monthly count, requires auth (401)
  POST /api/webhooks/stripe        — checkout.session.completed upgrades user to pro (new + upsert sub),
                                     customer.subscription.deleted downgrades to free,
                                     customer.subscription.updated updates sub record,
                                     invalid Stripe signature → 400, malformed payload → 400,
                                     unknown event type → 200 received,
                                     payment_intent.succeeded → 200 (no-op),
                                     missing customer/subscription in event data → skipped gracefully
"""

import json
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, AsyncMock

import pytest
from sqlalchemy import select

from app.deps import get_current_user
from app.main import app
from app.models import Subscription, UsageEvent, User


# ─── Helpers ──────────────────────────────────────────────────────────────────

async def _create_user(db_session, email: str, tier: str = "free",
                        stripe_customer_id: str | None = None) -> User:
    user = User(email=email, tier=tier, stripe_customer_id=stripe_customer_id)
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


def _user_override(user: User):
    """Return a FastAPI dependency override that yields the given user."""
    async def _dep():
        return user
    return _dep


def _make_stripe_sub(sub_id: str, customer_id: str, status: str = "active",
                     price_id: str = "price_pro") -> dict:
    now_ts = int(datetime.now(timezone.utc).timestamp())
    return {
        "id": sub_id,
        "customer": customer_id,
        "status": status,
        "items": {"data": [{"price": {"id": price_id}}]},
        "current_period_start": now_ts,
        "current_period_end": now_ts + 2592000,  # +30 days
    }


def _make_webhook_event(event_type: str, data_object: dict) -> dict:
    return {"type": event_type, "data": {"object": data_object}}


# ─── POST /api/billing/checkout ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_checkout_creates_new_stripe_customer(client, db_session):
    user = await _create_user(db_session, "checkout@example.com")
    app.dependency_overrides[get_current_user] = _user_override(user)

    mock_customer = MagicMock()
    mock_customer.id = "cus_newcustomer"
    mock_session = MagicMock()
    mock_session.url = "https://checkout.stripe.com/session/abc"

    try:
        with patch("app.routes.billing.stripe.Customer.create", return_value=mock_customer), \
             patch("app.routes.billing.stripe.checkout.Session.create", return_value=mock_session):
            resp = await client.post("/api/billing/checkout")
    finally:
        del app.dependency_overrides[get_current_user]

    assert resp.status_code == 200
    assert resp.json()["checkout_url"] == "https://checkout.stripe.com/session/abc"

    # Stripe customer ID saved to user
    await db_session.refresh(user)
    assert user.stripe_customer_id == "cus_newcustomer"


@pytest.mark.asyncio
async def test_checkout_reuses_existing_stripe_customer(client, db_session):
    user = await _create_user(db_session, "existing-cus@example.com",
                               stripe_customer_id="cus_existing123")
    app.dependency_overrides[get_current_user] = _user_override(user)

    mock_session = MagicMock()
    mock_session.url = "https://checkout.stripe.com/session/existing"

    try:
        with patch("app.routes.billing.stripe.Customer.create") as mock_create, \
             patch("app.routes.billing.stripe.checkout.Session.create", return_value=mock_session):
            resp = await client.post("/api/billing/checkout")
            mock_create.assert_not_called()  # must NOT create a new customer
    finally:
        del app.dependency_overrides[get_current_user]

    assert resp.status_code == 200
    assert resp.json()["checkout_url"] == "https://checkout.stripe.com/session/existing"


@pytest.mark.asyncio
async def test_checkout_already_pro_returns_400(client, db_session):
    user = await _create_user(db_session, "pro@example.com", tier="pro")
    app.dependency_overrides[get_current_user] = _user_override(user)

    try:
        resp = await client.post("/api/billing/checkout")
    finally:
        del app.dependency_overrides[get_current_user]

    assert resp.status_code == 400
    assert "Pro" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_checkout_already_enterprise_returns_400(client, db_session):
    user = await _create_user(db_session, "enterprise@example.com", tier="enterprise")
    app.dependency_overrides[get_current_user] = _user_override(user)

    try:
        resp = await client.post("/api/billing/checkout")
    finally:
        del app.dependency_overrides[get_current_user]

    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_checkout_unauthenticated_returns_401(client, db_session):
    resp = await client.post("/api/billing/checkout")
    assert resp.status_code == 401


# ─── GET /api/billing/usage ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_usage_free_user_returns_limit(client, db_session):
    user = await _create_user(db_session, "usage-free@example.com", tier="free")
    app.dependency_overrides[get_current_user] = _user_override(user)

    try:
        resp = await client.get("/api/billing/usage")
    finally:
        del app.dependency_overrides[get_current_user]

    assert resp.status_code == 200
    data = resp.json()
    assert data["tier"] == "free"
    assert data["monthly_generations_used"] == 0
    assert data["monthly_generations_limit"] is not None  # free has a limit


@pytest.mark.asyncio
async def test_usage_pro_user_no_limit(client, db_session):
    user = await _create_user(db_session, "usage-pro@example.com", tier="pro")
    app.dependency_overrides[get_current_user] = _user_override(user)

    try:
        resp = await client.get("/api/billing/usage")
    finally:
        del app.dependency_overrides[get_current_user]

    assert resp.status_code == 200
    data = resp.json()
    assert data["tier"] == "pro"
    assert data["monthly_generations_limit"] is None  # pro = unlimited


@pytest.mark.asyncio
async def test_usage_unauthenticated_returns_401(client):
    resp = await client.get("/api/billing/usage")
    assert resp.status_code == 401


# ─── POST /api/webhooks/stripe ────────────────────────────────────────────────

def _post_webhook(client, event: dict, sig: str = "t=1,v1=abc"):
    return client.post(
        "/api/webhooks/stripe",
        content=json.dumps(event).encode(),
        headers={"Stripe-Signature": sig, "Content-Type": "application/json"},
    )


@pytest.mark.asyncio
async def test_webhook_invalid_signature_returns_400(client, db_session):
    import stripe as _stripe

    with patch("app.routes.billing.stripe.Webhook.construct_event",
               side_effect=_stripe.error.SignatureVerificationError("bad", "sig")):
        resp = await _post_webhook(client, {"type": "test"})

    assert resp.status_code == 400
    assert "signature" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_webhook_malformed_payload_returns_400(client, db_session):
    with patch("app.routes.billing.stripe.Webhook.construct_event",
               side_effect=Exception("Cannot parse")):
        resp = await _post_webhook(client, {"type": "test"})

    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_webhook_unknown_event_type_returns_200(client, db_session):
    event = _make_webhook_event("unknown.event.type", {})
    with patch("app.routes.billing.stripe.Webhook.construct_event", return_value=event):
        resp = await _post_webhook(client, event)

    assert resp.status_code == 200
    assert resp.json() == {"received": True}


@pytest.mark.asyncio
async def test_webhook_payment_intent_succeeded_is_noop(client, db_session):
    event = _make_webhook_event("payment_intent.succeeded", {"id": "pi_123"})
    with patch("app.routes.billing.stripe.Webhook.construct_event", return_value=event):
        resp = await _post_webhook(client, event)

    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_webhook_checkout_completed_upgrades_user_to_pro(client, db_session):
    user = await _create_user(db_session, "upgrade@example.com",
                               stripe_customer_id="cus_upgrade123")
    sub_id = "sub_newpro456"
    stripe_sub = _make_stripe_sub(sub_id, "cus_upgrade123", status="active")

    session_obj = {
        "customer": "cus_upgrade123",
        "subscription": sub_id,
        "metadata": {"user_id": str(user.id)},
    }
    event = _make_webhook_event("checkout.session.completed", session_obj)

    with patch("app.routes.billing.stripe.Webhook.construct_event", return_value=event), \
         patch("app.routes.billing.stripe.Subscription.retrieve", return_value=stripe_sub):
        resp = await _post_webhook(client, event)

    assert resp.status_code == 200

    await db_session.refresh(user)
    assert user.tier == "pro"

    # Subscription row was created
    sub = await db_session.scalar(
        select(Subscription).where(Subscription.stripe_subscription_id == sub_id)
    )
    assert sub is not None
    assert sub.status == "active"


@pytest.mark.asyncio
async def test_webhook_checkout_completed_upserts_existing_sub(client, db_session):
    """Second checkout.session.completed for same sub_id updates the existing row."""
    user = await _create_user(db_session, "upsert@example.com",
                               stripe_customer_id="cus_upsert123")
    sub_id = "sub_existing789"

    # Pre-insert a Subscription row
    existing_sub = Subscription(
        user_id=user.id,
        stripe_subscription_id=sub_id,
        stripe_price_id="price_old",
        status="trialing",
        current_period_start=datetime.utcnow(),
        current_period_end=datetime.utcnow() + timedelta(days=14),
    )
    db_session.add(existing_sub)
    await db_session.commit()

    stripe_sub = _make_stripe_sub(sub_id, "cus_upsert123", status="active")
    session_obj = {
        "customer": "cus_upsert123",
        "subscription": sub_id,
        "metadata": {"user_id": str(user.id)},
    }
    event = _make_webhook_event("checkout.session.completed", session_obj)

    with patch("app.routes.billing.stripe.Webhook.construct_event", return_value=event), \
         patch("app.routes.billing.stripe.Subscription.retrieve", return_value=stripe_sub):
        resp = await _post_webhook(client, event)

    assert resp.status_code == 200

    await db_session.refresh(existing_sub)
    assert existing_sub.status == "active"


@pytest.mark.asyncio
async def test_webhook_checkout_completed_resolves_user_by_customer_id(client, db_session):
    """If user_id metadata is missing, user is resolved by stripe_customer_id."""
    user = await _create_user(db_session, "by-customer@example.com",
                               stripe_customer_id="cus_bycustomer")
    sub_id = "sub_bycustomer111"
    stripe_sub = _make_stripe_sub(sub_id, "cus_bycustomer", status="active")

    session_obj = {
        "customer": "cus_bycustomer",
        "subscription": sub_id,
        "metadata": {},  # no user_id
    }
    event = _make_webhook_event("checkout.session.completed", session_obj)

    with patch("app.routes.billing.stripe.Webhook.construct_event", return_value=event), \
         patch("app.routes.billing.stripe.Subscription.retrieve", return_value=stripe_sub):
        resp = await _post_webhook(client, event)

    assert resp.status_code == 200
    await db_session.refresh(user)
    assert user.tier == "pro"


@pytest.mark.asyncio
async def test_webhook_checkout_completed_unknown_user_skipped(client, db_session):
    """Event with unknown user_id metadata AND unknown customer_id is skipped gracefully."""
    session_obj = {
        "customer": "cus_nobody",
        "subscription": "sub_nobody",
        "metadata": {"user_id": str(uuid.uuid4())},  # valid UUID but no DB row
    }
    event = _make_webhook_event("checkout.session.completed", session_obj)

    with patch("app.routes.billing.stripe.Webhook.construct_event", return_value=event), \
         patch("app.routes.billing.stripe.Subscription.retrieve",
               return_value=_make_stripe_sub("sub_nobody", "cus_nobody")):
        resp = await _post_webhook(client, event)

    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_webhook_checkout_completed_missing_subscription_skipped(client, db_session):
    """Event with no subscription_id is skipped gracefully (no crash, no DB change)."""
    session_obj = {
        "customer": "cus_nosub",
        "subscription": None,
        "metadata": {},
    }
    event = _make_webhook_event("checkout.session.completed", session_obj)

    with patch("app.routes.billing.stripe.Webhook.construct_event", return_value=event):
        resp = await _post_webhook(client, event)

    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_webhook_subscription_deleted_downgrades_user_to_free(client, db_session):
    user = await _create_user(db_session, "downgrade@example.com",
                               tier="pro", stripe_customer_id="cus_downgrade")
    sub_id = "sub_downgrade999"

    existing_sub = Subscription(
        user_id=user.id,
        stripe_subscription_id=sub_id,
        stripe_price_id="price_pro",
        status="active",
        current_period_start=datetime.utcnow(),
        current_period_end=datetime.utcnow() + timedelta(days=30),
    )
    db_session.add(existing_sub)
    await db_session.commit()

    stripe_sub_obj = {
        "id": sub_id,
        "customer": "cus_downgrade",
        "status": "canceled",
        "current_period_start": None,
        "current_period_end": None,
    }
    event = _make_webhook_event("customer.subscription.deleted", stripe_sub_obj)

    with patch("app.routes.billing.stripe.Webhook.construct_event", return_value=event):
        resp = await _post_webhook(client, event)

    assert resp.status_code == 200
    await db_session.refresh(user)
    assert user.tier == "free"
    await db_session.refresh(existing_sub)
    assert existing_sub.status == "canceled"


@pytest.mark.asyncio
async def test_webhook_subscription_updated_updates_record(client, db_session):
    user = await _create_user(db_session, "subupdate@example.com",
                               tier="pro", stripe_customer_id="cus_subupdate")
    sub_id = "sub_update777"
    now_ts = int(datetime.now(timezone.utc).timestamp())

    existing_sub = Subscription(
        user_id=user.id,
        stripe_subscription_id=sub_id,
        stripe_price_id="price_pro",
        status="active",
        current_period_start=datetime.utcnow(),
        current_period_end=datetime.utcnow() + timedelta(days=30),
    )
    db_session.add(existing_sub)
    await db_session.commit()

    stripe_sub_obj = {
        "id": sub_id,
        "customer": "cus_subupdate",
        "status": "past_due",
        "current_period_start": now_ts,
        "current_period_end": now_ts + 2592000,
    }
    event = _make_webhook_event("customer.subscription.updated", stripe_sub_obj)

    with patch("app.routes.billing.stripe.Webhook.construct_event", return_value=event):
        resp = await _post_webhook(client, event)

    assert resp.status_code == 200
    await db_session.refresh(existing_sub)
    assert existing_sub.status == "past_due"

    # past_due → user downgraded to free
    await db_session.refresh(user)
    assert user.tier == "free"


@pytest.mark.asyncio
async def test_webhook_subscription_updated_unknown_sub_no_crash(client, db_session):
    """subscription.updated for an unknown sub_id (no DB row) should not crash."""
    user = await _create_user(db_session, "unknownsub@example.com",
                               tier="pro", stripe_customer_id="cus_unknownsub")

    stripe_sub_obj = {
        "id": "sub_completely_unknown",
        "customer": "cus_unknownsub",
        "status": "canceled",
        "current_period_start": None,
        "current_period_end": None,
    }
    event = _make_webhook_event("customer.subscription.updated", stripe_sub_obj)

    with patch("app.routes.billing.stripe.Webhook.construct_event", return_value=event):
        resp = await _post_webhook(client, event)

    assert resp.status_code == 200
    # User still downgraded since status is canceled and no active subs
    await db_session.refresh(user)
    assert user.tier == "free"


@pytest.mark.asyncio
async def test_webhook_subscription_deleted_missing_sub_id_skipped(client, db_session):
    """Event with no id field in subscription object is skipped gracefully."""
    stripe_sub_obj = {
        "id": None,
        "customer": "cus_nosub",
        "status": "canceled",
    }
    event = _make_webhook_event("customer.subscription.deleted", stripe_sub_obj)

    with patch("app.routes.billing.stripe.Webhook.construct_event", return_value=event):
        resp = await _post_webhook(client, event)

    assert resp.status_code == 200
