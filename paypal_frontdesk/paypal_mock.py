"""In-process mock PayPal server (stdlib http.server) for offline demos/tests.

Implements the same surface as ``paypal_client.py``:

- ``POST /v1/oauth2/token``
- ``POST /v2/checkout/orders`` / ``GET /v2/checkout/orders/{id}``
- ``POST /v2/checkout/orders/{id}/capture``
- ``POST /v2/payments/captures/{id}/refund``
- ``POST /v2/invoicing/invoices`` / ``POST /v2/invoicing/invoices/{id}/send``
- ``GET  /checkout/approve?token={order_id}`` — stands in for PayPal's
  hosted approval page: visiting it flips the order to APPROVED.

The lifecycle is real — an order starts CREATED, must be APPROVED (via the
link) before it can be captured, and a capture can be refunded — so error
paths (unapproved capture, expired order, double refund) behave like the
real API. Test hooks: ``fail_next_capture`` and ``expire_order()``.
"""

from __future__ import annotations

import base64
import itertools
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


class MockPayPalServer:
    """A thread-local, in-memory PayPal stand-in on an ephemeral port."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self.host = host
        self.port = port
        self.orders: dict[str, dict] = {}
        self.captures: dict[str, dict] = {}
        self.refunds: dict[str, dict] = {}
        self.invoices: dict[str, dict] = {}
        self.tokens_issued = 0
        self.auth_headers_seen: list[str] = []
        self.fail_next_capture = False
        self._counter = itertools.count(1)
        self._lock = threading.Lock()
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> "MockPayPalServer":
        handler = _make_handler(self)
        self._httpd = ThreadingHTTPServer((self.host, self.port), handler)
        self._httpd.daemon_threads = True
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="mock-paypal", daemon=True
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None

    def __enter__(self) -> "MockPayPalServer":
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    # -- state manipulation (used by the request handler and tests) ---------

    def _next_id(self, prefix: str) -> str:
        with self._lock:
            return f"{prefix}-{next(self._counter):06d}"

    def create_order(self, body: dict) -> dict:
        order_id = self._next_id("ORDER")
        order = {
            "id": order_id,
            "status": "CREATED",
            "intent": body.get("intent", "CAPTURE"),
            "purchase_units": body.get("purchase_units", []),
            "links": [
                {
                    "href": f"{self.base_url}/v2/checkout/orders/{order_id}",
                    "rel": "self",
                    "method": "GET",
                },
                {
                    "href": f"{self.base_url}/checkout/approve?token={order_id}",
                    "rel": "approve",
                    "method": "GET",
                },
                {
                    "href": f"{self.base_url}/v2/checkout/orders/{order_id}/capture",
                    "rel": "capture",
                    "method": "POST",
                },
            ],
        }
        self.orders[order_id] = order
        return order

    def approve_order(self, order_id: str) -> bool:
        order = self.orders.get(order_id)
        if order is None or order["status"] != "CREATED":
            return False
        order["status"] = "APPROVED"
        return True

    def capture_order(self, order_id: str) -> tuple[int, dict]:
        order = self.orders.get(order_id)
        if order is None:
            return 404, {"name": "RESOURCE_NOT_FOUND", "message": "order not found"}
        if self.fail_next_capture:
            self.fail_next_capture = False
            return 422, {
                "name": "UNPROCESSABLE_ENTITY",
                "message": "simulated capture failure (fail_next_capture)",
            }
        if order["status"] == "EXPIRED":
            return 422, {
                "name": "UNPROCESSABLE_ENTITY",
                "message": "Order has expired",
            }
        if order["status"] != "APPROVED":
            return 422, {
                "name": "UNPROCESSABLE_ENTITY",
                "message": "Order must be approved by the buyer before capture",
            }
        capture_id = self._next_id("CAPTURE")
        unit = (order.get("purchase_units") or [{}])[0]
        amount = unit.get("amount", {})
        capture = {
            "id": capture_id,
            "status": "COMPLETED",
            "amount": amount,
            "order_id": order_id,
        }
        self.captures[capture_id] = capture
        order["status"] = "COMPLETED"
        return 201, {
            "id": order_id,
            "status": "COMPLETED",
            "purchase_units": [
                {"payments": {"captures": [capture]}}
            ],
        }

    def refund_capture(self, capture_id: str, body: dict | None) -> tuple[int, dict]:
        capture = self.captures.get(capture_id)
        if capture is None:
            return 404, {"name": "RESOURCE_NOT_FOUND", "message": "capture not found"}
        if capture.get("refunded"):
            return 422, {
                "name": "UNPROCESSABLE_ENTITY",
                "message": "Capture has already been fully refunded",
            }
        capture["refunded"] = True
        capture["status"] = "REFUNDED"
        refund_id = self._next_id("REFUND")
        amount = (body or {}).get("amount") or capture.get("amount", {})
        refund = {
            "id": refund_id,
            "status": "COMPLETED",
            "amount": amount,
            "capture_id": capture_id,
        }
        self.refunds[refund_id] = refund
        return 201, refund

    def create_invoice(self, body: dict) -> dict:
        invoice_id = self._next_id("INV")
        invoice = {
            "id": invoice_id,
            "status": "DRAFT",
            "detail": body.get("detail", {}),
            "items": body.get("items", []),
            "amount": body.get("amount", {}),
            "primary_recipients": body.get("primary_recipients", []),
        }
        self.invoices[invoice_id] = invoice
        return invoice

    def send_invoice(self, invoice_id: str) -> tuple[int, dict]:
        invoice = self.invoices.get(invoice_id)
        if invoice is None:
            return 404, {"name": "RESOURCE_NOT_FOUND", "message": "invoice not found"}
        invoice["status"] = "SENT"
        return 200, {"id": invoice_id, "status": "SENT"}

    def expire_order(self, order_id: str) -> None:
        """Test hook: mark an order EXPIRED (PayPal expires stale orders)."""
        if order_id in self.orders:
            self.orders[order_id]["status"] = "EXPIRED"

    def issue_token(self, auth_header: str | None) -> tuple[int, dict]:
        if not auth_header or not auth_header.startswith("Basic "):
            return 401, {"error": "invalid_client", "error_description": "missing auth"}
        try:
            base64.b64decode(auth_header[6:]).decode("utf-8")
        except Exception:
            return 401, {"error": "invalid_client"}
        self.tokens_issued += 1
        return 200, {
            "access_token": f"MOCK-TOKEN-{self.tokens_issued}",
            "token_type": "Bearer",
            "expires_in": 32400,
        }


def _make_handler(server: MockPayPalServer) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        # Silence per-request stderr logging in demos/tests.
        def log_message(self, fmt: str, *args: object) -> None:
            pass

        # -- helpers ---------------------------------------------------------

        def _json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _html(self, status: int, page: str) -> None:
            body = page.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            raw = self.rfile.read(length).decode("utf-8")
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {}

        # -- routes ------------------------------------------------------------

        def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
            parsed = urlparse(self.path)
            if parsed.path == "/checkout/approve":
                token = parse_qs(parsed.query).get("token", [""])[0]
                if server.approve_order(token):
                    self._html(
                        200,
                        "<html><body style='font-family:sans-serif;text-align:center;"
                        "margin-top:4em'>"
                        "<h2>Payment approved</h2>"
                        f"<p>Order <code>{token}</code> is approved. "
                        "Return to the chat — the agent will capture your "
                        "deposit and confirm your booking.</p></body></html>",
                    )
                else:
                    self._html(
                        400,
                        "<html><body><h2>Cannot approve</h2><p>Unknown or "
                        "already-processed order.</p></body></html>",
                    )
                return
            if parsed.path.startswith("/v2/checkout/orders/"):
                order_id = parsed.path.rsplit("/", 1)[-1]
                order = server.orders.get(order_id)
                if order is None:
                    self._json(404, {"name": "RESOURCE_NOT_FOUND"})
                else:
                    self._json(200, order)
                return
            self._json(404, {"name": "NOT_FOUND", "path": parsed.path})

        def do_POST(self) -> None:  # noqa: N802 (stdlib naming)
            parsed = urlparse(self.path)
            path = parsed.path

            if path == "/v1/oauth2/token":
                auth = self.headers.get("Authorization")
                server.auth_headers_seen.append(auth or "")
                status, payload = server.issue_token(auth)
                self._json(status, payload)
                return

            if path == "/v2/checkout/orders":
                self._json(201, server.create_order(self._read_body()))
                return

            if path.startswith("/v2/checkout/orders/") and path.endswith("/capture"):
                order_id = path[len("/v2/checkout/orders/") : -len("/capture")]
                status, payload = server.capture_order(order_id)
                self._json(status, payload)
                return

            if path.startswith("/v2/payments/captures/") and path.endswith("/refund"):
                capture_id = path[len("/v2/payments/captures/") : -len("/refund")]
                status, payload = server.refund_capture(capture_id, self._read_body())
                self._json(status, payload)
                return

            if path == "/v2/invoicing/invoices":
                self._json(201, server.create_invoice(self._read_body()))
                return

            if path.startswith("/v2/invoicing/invoices/") and path.endswith("/send"):
                invoice_id = path[len("/v2/invoicing/invoices/") : -len("/send")]
                status, payload = server.send_invoice(invoice_id)
                self._json(status, payload)
                return

            self._json(404, {"name": "NOT_FOUND", "path": path})

    return Handler
