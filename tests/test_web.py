"""Tests for the web simulator: page shell, static assets, and live API."""

from __future__ import annotations

import json
import threading
import urllib.request

import pytest

from paypal_frontdesk.agent import FrontDeskAgent
from paypal_frontdesk.config import Business
from paypal_frontdesk.paypal_client import PayPalClient
from paypal_frontdesk.web import make_handler

from http.server import ThreadingHTTPServer


@pytest.fixture()
def server(business: Business, mock_paypal):
    client = PayPalClient(mock_paypal.base_url, "mock-id", "mock-secret")
    agent = FrontDeskAgent(business, client)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(agent, mock_paypal))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", agent
    httpd.shutdown()
    httpd.server_close()


def get(url: str) -> tuple[int, bytes, str]:
    with urllib.request.urlopen(url, timeout=10) as resp:
        return resp.status, resp.read(), resp.headers.get("Content-Type", "")


def test_page_references_ag_grid(server):
    base, _ = server
    status, body, ctype = get(base + "/")
    assert status == 200
    html = body.decode()
    assert "ag-theme-alpine-dark" in html
    assert "/static/ag-grid-community.min.js" in html
    assert "createGrid" in html


def test_static_ag_grid_served(server):
    base, _ = server
    status, body, ctype = get(base + "/static/ag-grid-community.min.js")
    assert status == 200
    assert "javascript" in ctype
    assert b"agGrid" in body
    assert len(body) > 500_000  # the real vendored library, not a stub


def test_static_unknown_is_404(server):
    base, _ = server
    with pytest.raises(urllib.error.HTTPError) as exc:
        get(base + "/static/nope.js")
    assert exc.value.code == 404


def test_state_endpoint_shape(server):
    base, _ = server
    status, body, _ = get(base + "/api/state")
    payload = json.loads(body)
    assert payload["state"] == "COLLECTING"
    assert payload["bookings"] == []
    assert payload["trail"] == []


def test_full_booking_flow_over_http(server):
    base, _ = server
    req = urllib.request.Request(
        base + "/api/message",
        data=json.dumps({"message": "Hi, I'm Dana Lee. I'd like a full interior detail next Friday at 2 pm. My number is 555-214-8690."}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        first = json.loads(resp.read())
    assert first["state"] == "CONFIRMING"

    for msg in ("yes", "yes"):
        req = urllib.request.Request(
            base + "/api/message",
            data=json.dumps({"message": msg}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            out = json.loads(resp.read())
    assert out["state"] == "AWAITING_DEPOSIT"
    assert out["bookings"][0]["order_id"].startswith("ORDER-")
    assert any(e["kind"] == "order_created" for e in out["trail"])
