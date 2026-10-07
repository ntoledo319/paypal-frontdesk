"""PayPal REST client — pure stdlib (urllib), env-configured credentials.

Implements the exact subset of the PayPal v2 APIs the agent needs:

- ``POST /v1/oauth2/token``                  (client credentials grant)
- ``POST /v2/checkout/orders``               (create order)
- ``GET  /v2/checkout/orders/{id}``          (check approval status)
- ``POST /v2/checkout/orders/{id}/capture``  (capture the money)
- ``POST /v2/payments/captures/{id}/refund`` (refund a capture)
- ``POST /v2/invoicing/invoices``            (create invoice)
- ``POST /v2/invoicing/invoices/{id}/send``  (send invoice)

The same code runs unchanged against the bundled mock server
(``paypal_mock.py``) and against ``https://api-m.sandbox.paypal.com`` — only
``PAYPAL_BASE_URL`` and the credentials differ. Credentials are read from
environment variables at runtime and never stored, logged, or printed.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "https://api-m.sandbox.paypal.com"


class PayPalError(Exception):
    """A PayPal API call failed. Carries the HTTP status and response body."""

    def __init__(self, message: str, status: int | None = None, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body


class PayPalClient:
    def __init__(
        self,
        base_url: str,
        client_id: str,
        client_secret: str,
        timeout: float = 20.0,
    ) -> None:
        if not client_id or not client_secret:
            raise ValueError("PayPal client_id and client_secret are required")
        self.base_url = base_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self.timeout = timeout
        self._token: str | None = None

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> "PayPalClient":
        """Build a client from PAYPAL_BASE_URL / PAYPAL_CLIENT_ID /
        PAYPAL_CLIENT_SECRET. Raises ValueError if credentials are missing."""
        env = os.environ if environ is None else environ
        client_id = env.get("PAYPAL_CLIENT_ID", "").strip()
        client_secret = env.get("PAYPAL_CLIENT_SECRET", "").strip()
        if not client_id or not client_secret:
            raise ValueError(
                "PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET must be set "
                "(get sandbox credentials at https://developer.paypal.com)"
            )
        return cls(
            base_url=env.get("PAYPAL_BASE_URL", DEFAULT_BASE_URL),
            client_id=client_id,
            client_secret=client_secret,
        )

    # -- v2 checkout/orders -------------------------------------------------

    def create_order(self, amount: str, currency: str, description: str) -> dict:
        """Create an order; the buyer approves it at the 'approve' link."""
        return self._request(
            "POST",
            "/v2/checkout/orders",
            {
                "intent": "CAPTURE",
                "purchase_units": [
                    {
                        "description": description,
                        "amount": {"currency_code": currency, "value": amount},
                    }
                ],
            },
        )

    def get_order(self, order_id: str) -> dict:
        return self._request("GET", f"/v2/checkout/orders/{order_id}")

    def capture_order(self, order_id: str) -> dict:
        """Capture an approved order — this is the moment money moves."""
        return self._request("POST", f"/v2/checkout/orders/{order_id}/capture")

    # -- v2 payments/refunds -------------------------------------------------

    def refund_capture(
        self, capture_id: str, amount: str | None = None, currency: str | None = None
    ) -> dict:
        body = None
        if amount is not None and currency is not None:
            body = {"amount": {"currency_code": currency, "value": amount}}
        return self._request("POST", f"/v2/payments/captures/{capture_id}/refund", body)

    # -- v2 invoicing ---------------------------------------------------------

    def create_invoice(
        self,
        amount: str,
        currency: str,
        item_name: str,
        recipient_email: str | None = None,
        note: str = "",
    ) -> dict:
        invoice: dict = {
            "detail": {
                "currency_code": currency,
                "note": note or item_name,
            },
            "items": [
                {
                    "name": item_name,
                    "quantity": "1",
                    "unit_amount": {"currency_code": currency, "value": amount},
                }
            ],
            "amount": {
                "breakdown": {
                    "item_total": {"currency_code": currency, "value": amount}
                }
            },
        }
        if recipient_email:
            invoice["primary_recipients"] = [
                {"billing_info": {"email_address": recipient_email}}
            ]
        return self._request("POST", "/v2/invoicing/invoices", invoice)

    def send_invoice(self, invoice_id: str) -> dict:
        return self._request(
            "POST",
            f"/v2/invoicing/invoices/{invoice_id}/send",
            {"send_to_recipient": True},
        )

    # -- transport ------------------------------------------------------------

    def _get_token(self) -> str:
        if self._token:
            return self._token
        credentials = base64.b64encode(
            f"{self._client_id}:{self._client_secret}".encode("utf-8")
        ).decode("ascii")
        request = urllib.request.Request(
            f"{self.base_url}/v1/oauth2/token",
            data=b"grant_type=client_credentials",
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise PayPalError(
                "PayPal OAuth token request failed",
                status=exc.code,
                body=_safe_body(exc),
            ) from exc
        except urllib.error.URLError as exc:
            raise PayPalError(f"PayPal unreachable: {exc.reason}") from exc
        token = payload.get("access_token")
        if not token:
            raise PayPalError("PayPal OAuth response had no access_token")
        self._token = token
        return token

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        return self._request_once(method, path, body, retry_on_401=True)

    def _request_once(
        self, method: str, path: str, body: dict | None, retry_on_401: bool
    ) -> dict:
        headers = {
            "Authorization": f"Bearer {self._get_token()}",
            "Accept": "application/json",
        }
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            if exc.code == 401 and retry_on_401:
                # Token may have expired; refresh once and retry.
                self._token = None
                return self._request_once(method, path, body, retry_on_401=False)
            raise PayPalError(
                f"PayPal {method} {path} failed with HTTP {exc.code}",
                status=exc.code,
                body=_safe_body(exc),
            ) from exc
        except urllib.error.URLError as exc:
            raise PayPalError(f"PayPal unreachable: {exc.reason}") from exc
        return json.loads(raw) if raw else {}


def _safe_body(exc: urllib.error.HTTPError) -> str:
    try:
        return exc.read().decode("utf-8", errors="replace")
    except Exception:
        return ""


def find_link(resource: dict, rel: str) -> str | None:
    """Find an HATEOAS link href by rel in a PayPal resource."""
    for link in resource.get("links", []):
        if link.get("rel") == rel:
            return link.get("href")
    return None


def extract_capture_id(capture_response: dict) -> str | None:
    """Pull the capture id out of a capture-order response."""
    for unit in capture_response.get("purchase_units", []):
        for capture in unit.get("payments", {}).get("captures", []):
            if capture.get("id"):
                return capture["id"]
    return None
