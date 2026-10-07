"""Mock server + REST client round-trip tests (all offline, ephemeral ports)."""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request

import pytest

from paypal_frontdesk.paypal_client import (
    PayPalClient,
    PayPalError,
    extract_capture_id,
    find_link,
)

from conftest import approve_order


def _post(url: str, body: dict | None = None, headers: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers or {}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


# -- mock server behavior ------------------------------------------------------


def test_token_endpoint(mock_paypal):
    auth = base64.b64encode(b"id:secret").decode()
    status, payload = _post(
        f"{mock_paypal.base_url}/v1/oauth2/token",
        headers={"Authorization": f"Basic {auth}"},
    )
    assert status == 200
    assert payload["access_token"].startswith("MOCK-TOKEN")
    assert payload["token_type"] == "Bearer"


def test_token_endpoint_rejects_missing_auth(mock_paypal):
    status, _ = _post(f"{mock_paypal.base_url}/v1/oauth2/token")
    assert status == 401


def test_create_order_shape(mock_paypal, client):
    order = client.create_order("30.00", "USD", "deposit")
    assert order["status"] == "CREATED"
    assert order["id"].startswith("ORDER-")
    assert find_link(order, "approve").startswith(mock_paypal.base_url)
    assert find_link(order, "self").endswith(order["id"])


def test_approve_link_flips_status(mock_paypal, client):
    order = client.create_order("30.00", "USD", "deposit")
    assert approve_order(client, order)
    assert client.get_order(order["id"])["status"] == "APPROVED"


def test_capture_requires_approval(mock_paypal, client):
    order = client.create_order("30.00", "USD", "deposit")
    with pytest.raises(PayPalError) as err:
        client.capture_order(order["id"])
    assert err.value.status == 422


def test_capture_approved_order(mock_paypal, client):
    order = client.create_order("30.00", "USD", "deposit")
    approve_order(client, order)
    capture = client.capture_order(order["id"])
    assert capture["status"] == "COMPLETED"
    capture_id = extract_capture_id(capture)
    assert capture_id and capture_id.startswith("CAPTURE-")
    assert client.get_order(order["id"])["status"] == "COMPLETED"


def test_fail_next_capture_hook(mock_paypal, client):
    order = client.create_order("30.00", "USD", "deposit")
    approve_order(client, order)
    mock_paypal.fail_next_capture = True
    with pytest.raises(PayPalError) as err:
        client.capture_order(order["id"])
    assert err.value.status == 422
    # Next attempt succeeds — the failure flag is one-shot.
    assert client.capture_order(order["id"])["status"] == "COMPLETED"


def test_expired_order_cannot_capture(mock_paypal, client):
    order = client.create_order("30.00", "USD", "deposit")
    approve_order(client, order)
    mock_paypal.expire_order(order["id"])
    assert client.get_order(order["id"])["status"] == "EXPIRED"
    with pytest.raises(PayPalError):
        client.capture_order(order["id"])


def test_refund_round_trip(mock_paypal, client):
    order = client.create_order("30.00", "USD", "deposit")
    approve_order(client, order)
    capture_id = extract_capture_id(client.capture_order(order["id"]))
    refund = client.refund_capture(capture_id, amount="30.00", currency="USD")
    assert refund["id"].startswith("REFUND-")
    assert refund["status"] == "COMPLETED"
    # Double refund is rejected.
    with pytest.raises(PayPalError) as err:
        client.refund_capture(capture_id)
    assert err.value.status == 422


def test_refund_unknown_capture_404(mock_paypal, client):
    with pytest.raises(PayPalError) as err:
        client.refund_capture("CAPTURE-999999")
    assert err.value.status == 404


def test_invoice_create_and_send(mock_paypal, client):
    invoice = client.create_invoice(
        "90.00", "USD", "balance for detail", recipient_email="dana@example.com"
    )
    assert invoice["id"].startswith("INV-")
    assert invoice["status"] == "DRAFT"
    sent = client.send_invoice(invoice["id"])
    assert sent["status"] == "SENT"
    assert mock_paypal.invoices[invoice["id"]]["status"] == "SENT"


# -- client behavior ------------------------------------------------------------


def test_from_env_requires_credentials():
    with pytest.raises(ValueError):
        PayPalClient.from_env({})
    with pytest.raises(ValueError):
        PayPalClient.from_env({"PAYPAL_CLIENT_ID": "x"})


def test_from_env_uses_base_url_override(mock_paypal):
    client = PayPalClient.from_env(
        {
            "PAYPAL_BASE_URL": mock_paypal.base_url,
            "PAYPAL_CLIENT_ID": "id",
            "PAYPAL_CLIENT_SECRET": "secret",
        }
    )
    order = client.create_order("10.00", "USD", "x")
    assert order["id"].startswith("ORDER-")


def test_client_sends_basic_auth_for_token(mock_paypal, client):
    client.create_order("10.00", "USD", "x")
    expected = base64.b64encode(b"test-id:test-secret").decode()
    assert f"Basic {expected}" in mock_paypal.auth_headers_seen


def test_client_caches_token(mock_paypal, client):
    client.create_order("10.00", "USD", "a")
    client.create_order("10.00", "USD", "b")
    assert mock_paypal.tokens_issued == 1


def test_client_requires_credentials_upfront():
    with pytest.raises(ValueError):
        PayPalClient("http://x", "", "secret")


def test_unknown_order_404(mock_paypal, client):
    with pytest.raises(PayPalError) as err:
        client.get_order("ORDER-999999")
    assert err.value.status == 404
