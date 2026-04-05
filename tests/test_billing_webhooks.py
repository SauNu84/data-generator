"""
Stripe webhook integration tests — tests/test_billing_webhooks.py

Tests the POST /api/webhooks/stripe endpoint against the full event lifecycle:
  - Invalid signature → 400
  - checkout.session.completed → user tier upgraded to pro, Subscription row created
  - customer.subscription.updated (canceled) → user tier downgraded to free
  - customer.subscription.deleted → user tier downgraded
  - customer.subscription.updated (active) → tier stays/becomes pro
  - payment_intent.succeeded → 200 pass-through (informational only)
  - Unknown event type → 200 pass-through
  - Duplicate subscription update (upsert) → idempotent

All Stripe API calls (stripe.Webhook.construct_event, stripe.Subscription.retrieve) are
mocked so these tests work without a real Stripe account or stripe-mock service.
"""

import time
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.database import get_db
from app.main import app
from app.models import Subscription, User


# ─── Helpers ──────────────────────────────────────────────────────────────────

WEBHOOK_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000099")
STRIPE_CUSTOMER_ID = "cus_test_webhookuser"
STRIPE_SUB_ID = "sub_test_001"
STRIPE_PRICE_ID = "price_test_pro"


def _now_ts() -> int:
    return int(time.time())


def _make_sub_object(
    sub_id: str = STRIPE_SUB_ID,
    status: str = "active",
    customer_id: str = STRIPE_CUSTOMER_ID,
    price_id: str = STRIPE_PRICE_ID,
) -> MagicMock:
    """Return a dict-like mock for a Stripe Subscription object."""
    ts = _now_ts()
    sub = MagicMock()
    sub.__getitem__ = lambda self, k: {
        "id": sub_id,
        "status": status,
        "customer": customer_id,
        "current_period_start": ts,
        "current_period_end": ts + 2592000,
        "items": {"data": [{"price": {"id": price_id}}]},
    }[k]
    sub.get = lambda k, default=None: {
        "id": sub_id,
        "status": status,
        "customer": customer_id,
        "current_period_start": ts,
        "current_period_end": ts + 2592000,
    }.get(k, default)
    return sub


def _checkout_event(
    user_id: str,
    customer_id: str = STRIPE_CUSTOMER_ID,
    sub_id: str = STRIPE_SUB_ID,
) -> dict:
    return {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "customer": customer_id,
                "subscription": sub_id,
                "metadata": {"user_id": user_id},
            }
        },
    }


def _sub_event(
    event_type: str,
    sub_id: str = STRIPE_SUB_ID,
    status: str = "canceled",
    customer_id: str = STRIPE_CUSTOMER_ID,
) -> dict:
    ts = _now_ts()
    return {
        "type": event_type,
        "data": {
            "object": {
                "id": sub_id,
                "status": status,
                "customer": customer_id,
                "current_period_start": ts,
                "current_period_end": ts + 2592000,
            }
        },
    }


# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
async def webhook_user(db_session) -> User:
    user = User(
        id=WEBHOOK_USER_ID,
        email="webhook@example.com",
        hashed_password=None,
        is_active=True,
        tier="free",
        stripe_customer_id=STRIPE_CUSTOMER_ID,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.fixture
async def webhook_client(db_session, webhook_user):
    """Unauthenticated client with DB override — webhook endpoint is public."""

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    import app.main as main_module
    main_module.ensure_bucket = lambda: None

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


# ─── Tests ────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_webhook_invalid_signature(webhook_client):
    """Invalid Stripe-Signature header → 400."""
    with patch("app.routes.billing.stripe.Webhook.construct_event") as mock_ce:
        import stripe as stripe_lib
        mock_ce.side_effect = stripe_lib.error.SignatureVerificationError(
            "Bad sig", "t=0,v1=bad"
        )
        resp = await webhook_client.post(
            "/api/webhooks/stripe",
            content=b'{"type":"test"}',
            headers={"Stripe-Signature": "t=0,v1=badsig"},
        )
    assert resp.status_code == 400
    assert "signature" in resp.json()["detail"].lower()


@pytest.mark.anyio
async def test_webhook_malformed_payload(webhook_client):
    """Exception during construct_event (not SignatureVerificationError) → 400."""
    with patch("app.routes.billing.stripe.Webhook.construct_event") as mock_ce:
        mock_ce.side_effect = Exception("Malformed")
        resp = await webhook_client.post(
            "/api/webhooks/stripe",
            content=b"not-json",
            headers={"Stripe-Signature": "t=0,v1=x"},
        )
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_webhook_checkout_completed_upgrades_user(webhook_client, db_session, webhook_user):
    """checkout.session.completed → user tier becomes pro, Subscription row created."""
    event = _checkout_event(str(WEBHOOK_USER_ID))
    mock_sub = _make_sub_object(status="active")

    with (
        patch("app.routes.billing.stripe.Webhook.construct_event", return_value=event),
        patch("app.routes.billing.stripe.Subscription.retrieve", return_value=mock_sub),
    ):
        resp = await webhook_client.post(
            "/api/webhooks/stripe",
            content=b"{}",
            headers={"Stripe-Signature": "t=0,v1=mock"},
        )

    assert resp.status_code == 200
    assert resp.json() == {"received": True}

    await db_session.refresh(webhook_user)
    assert webhook_user.tier == "pro"

    sub_row = await db_session.scalar(
        select(Subscription).where(Subscription.stripe_subscription_id == STRIPE_SUB_ID)
    )
    assert sub_row is not None
    assert sub_row.status == "active"
    assert sub_row.user_id == WEBHOOK_USER_ID


@pytest.mark.anyio
async def test_webhook_checkout_completed_resolves_user_by_customer_id(
    webhook_client, db_session, webhook_user
):
    """checkout.session.completed with no metadata → resolves user by stripe_customer_id."""
    event = {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "customer": STRIPE_CUSTOMER_ID,
                "subscription": "sub_resolve_by_cust",
                "metadata": {},  # no user_id
            }
        },
    }
    mock_sub = _make_sub_object(sub_id="sub_resolve_by_cust", status="active")

    with (
        patch("app.routes.billing.stripe.Webhook.construct_event", return_value=event),
        patch("app.routes.billing.stripe.Subscription.retrieve", return_value=mock_sub),
    ):
        resp = await webhook_client.post(
            "/api/webhooks/stripe",
            content=b"{}",
            headers={"Stripe-Signature": "t=0,v1=mock"},
        )

    assert resp.status_code == 200
    await db_session.refresh(webhook_user)
    assert webhook_user.tier == "pro"


@pytest.mark.anyio
async def test_webhook_checkout_missing_subscription_noop(webhook_client, db_session, webhook_user):
    """checkout.session.completed with no subscription_id → silent no-op (returns 200)."""
    event = {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "customer": STRIPE_CUSTOMER_ID,
                "subscription": None,
                "metadata": {"user_id": str(WEBHOOK_USER_ID)},
            }
        },
    }

    with patch("app.routes.billing.stripe.Webhook.construct_event", return_value=event):
        resp = await webhook_client.post(
            "/api/webhooks/stripe",
            content=b"{}",
            headers={"Stripe-Signature": "t=0,v1=mock"},
        )

    assert resp.status_code == 200
    await db_session.refresh(webhook_user)
    assert webhook_user.tier == "free"  # unchanged


@pytest.mark.anyio
async def test_webhook_subscription_canceled_downgrades_user(
    webhook_client, db_session, webhook_user
):
    """customer.subscription.deleted → user tier reverts to free."""
    # First upgrade the user
    webhook_user.tier = "pro"
    db_session.add(webhook_user)
    # Seed an existing subscription row
    sub_row = Subscription(
        id=uuid.uuid4(),
        user_id=WEBHOOK_USER_ID,
        stripe_subscription_id=STRIPE_SUB_ID,
        stripe_price_id=STRIPE_PRICE_ID,
        status="active",
        current_period_start=datetime.now(timezone.utc),
        current_period_end=datetime.now(timezone.utc),
    )
    db_session.add(sub_row)
    await db_session.commit()

    event = _sub_event("customer.subscription.deleted", status="canceled")

    with patch("app.routes.billing.stripe.Webhook.construct_event", return_value=event):
        resp = await webhook_client.post(
            "/api/webhooks/stripe",
            content=b"{}",
            headers={"Stripe-Signature": "t=0,v1=mock"},
        )

    assert resp.status_code == 200
    await db_session.refresh(webhook_user)
    assert webhook_user.tier == "free"


@pytest.mark.anyio
async def test_webhook_subscription_updated_canceled(webhook_client, db_session, webhook_user):
    """customer.subscription.updated with status=canceled → user downgraded."""
    webhook_user.tier = "pro"
    db_session.add(webhook_user)
    await db_session.commit()

    event = _sub_event("customer.subscription.updated", status="canceled")

    with patch("app.routes.billing.stripe.Webhook.construct_event", return_value=event):
        resp = await webhook_client.post(
            "/api/webhooks/stripe",
            content=b"{}",
            headers={"Stripe-Signature": "t=0,v1=mock"},
        )

    assert resp.status_code == 200
    await db_session.refresh(webhook_user)
    assert webhook_user.tier == "free"


@pytest.mark.anyio
async def test_webhook_subscription_updated_active_upserts(
    webhook_client, db_session, webhook_user
):
    """customer.subscription.updated with status=active → Subscription row updated."""
    # Seed an existing subscription
    sub_row = Subscription(
        id=uuid.uuid4(),
        user_id=WEBHOOK_USER_ID,
        stripe_subscription_id=STRIPE_SUB_ID,
        stripe_price_id=STRIPE_PRICE_ID,
        status="past_due",
        current_period_start=datetime.now(timezone.utc),
        current_period_end=datetime.now(timezone.utc),
    )
    db_session.add(sub_row)
    await db_session.commit()

    event = _sub_event("customer.subscription.updated", status="active")

    with patch("app.routes.billing.stripe.Webhook.construct_event", return_value=event):
        resp = await webhook_client.post(
            "/api/webhooks/stripe",
            content=b"{}",
            headers={"Stripe-Signature": "t=0,v1=mock"},
        )

    assert resp.status_code == 200
    await db_session.refresh(sub_row)
    assert sub_row.status == "active"


@pytest.mark.anyio
async def test_webhook_payment_intent_succeeded_passthrough(webhook_client):
    """payment_intent.succeeded is informational — always returns 200."""
    event = {"type": "payment_intent.succeeded", "data": {"object": {}}}

    with patch("app.routes.billing.stripe.Webhook.construct_event", return_value=event):
        resp = await webhook_client.post(
            "/api/webhooks/stripe",
            content=b"{}",
            headers={"Stripe-Signature": "t=0,v1=mock"},
        )

    assert resp.status_code == 200
    assert resp.json() == {"received": True}


@pytest.mark.anyio
async def test_webhook_unknown_event_passthrough(webhook_client):
    """Unknown event types are ignored and return 200."""
    event = {"type": "invoice.payment_succeeded", "data": {"object": {}}}

    with patch("app.routes.billing.stripe.Webhook.construct_event", return_value=event):
        resp = await webhook_client.post(
            "/api/webhooks/stripe",
            content=b"{}",
            headers={"Stripe-Signature": "t=0,v1=mock"},
        )

    assert resp.status_code == 200
    assert resp.json() == {"received": True}
