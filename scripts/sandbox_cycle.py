#!/usr/bin/env python3
"""Live PayPal sandbox verification cycle for paypal-frontdesk.

Runs the exact PayPal v2 calls the agent makes, against the REAL sandbox
(api-m.sandbox.paypal.com), using env credentials — proving the README claim
that the same client code works against PayPal's sandbox unchanged.

Prereqs (one-time, via developer.paypal.com with the owner's login):
  1. Sandbox REST app -> copy client id / secret.
  2. Sandbox BUYER account (email + password) for the approval step.

Usage:
  PAYPAL_CLIENT_ID=... PAYPAL_CLIENT_SECRET=... \
      python3 scripts/sandbox_cycle.py --buyer-email YOUR_SANDBOX_BUYER_EMAIL

The script pauses after creating the order and prints the approval link.
Open it in a browser, log in as the sandbox buyer, approve, then press Enter.
Output is a JSON summary (order/capture/refund/invoice IDs + statuses) that
goes verbatim into SANDBOX-VERIFICATION.md. No secrets are printed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from paypal_frontdesk.paypal_client import (
    PayPalClient,
    extract_capture_id,
    find_link,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--buyer-email", required=True,
        help="email of your PayPal sandbox personal (buyer) test account",
    )
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", args.buyer_email):
        parser.error("provide the actual sandbox buyer account email")
    # This verification script must never move production money or send an
    # invoice from a production account, even if the shell has live settings.
    base_url = os.environ.get("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com")
    if base_url.rstrip("/") != "https://api-m.sandbox.paypal.com":
        parser.error("PAYPAL_BASE_URL must be https://api-m.sandbox.paypal.com")
    client = PayPalClient.from_env()  # base URL defaults to the real sandbox
    print(f"[sandbox] base_url = {client.base_url}")
    result: dict[str, object] = {"base_url": client.base_url}

    order = client.create_order("30.00", "USD", "sandbox verification deposit")
    order_id = order.get("id")
    result["order"] = {"id": order_id, "status": order.get("status")}
    print(f"[sandbox] order created: {order_id} status={order.get('status')}")

    approve = find_link(order, "approve")
    if not order_id or not approve:
        print("[sandbox] order response has no ID or approval link; aborting")
        return 1
    print(f"[sandbox] approve link: {approve}")
    input("[sandbox] approve as the sandbox buyer in a browser, then press Enter...")

    state = client.get_order(order_id)
    result["order_after_approval"] = {"status": state.get("status")}
    if state.get("status") != "APPROVED":
        print(f"[sandbox] order not approved (status={state.get('status')}); aborting")
        print(json.dumps(result, indent=2))
        return 1

    capture = client.capture_order(order_id)
    capture_id = extract_capture_id(capture)
    result["capture"] = {"order_status": capture.get("status"), "capture_id": capture_id}
    print(f"[sandbox] captured: {capture_id} order_status={capture.get('status')}")

    if capture.get("status") != "COMPLETED" or not capture_id:
        print("[sandbox] capture not completed; aborting before refund or invoice")
        print(json.dumps(result, indent=2))
        return 1

    refund = client.refund_capture(capture_id)
    result["refund"] = {"id": refund.get("id"), "status": refund.get("status")}
    print(f"[sandbox] refunded: {refund.get('id')} status={refund.get('status')}")

    invoice = client.create_invoice(
        "90.00",
        "USD",
        "sandbox verification balance",
        recipient_email=args.buyer_email,
        note="sandbox verification balance invoice",
    )
    invoice_id = invoice.get("id")
    if not invoice_id:
        print("[sandbox] invoice response has no ID; aborting")
        print(json.dumps(result, indent=2))
        return 1
    sent = client.send_invoice(invoice_id)
    result["invoice"] = {"id": invoice_id, "send_status": sent.get("status", "sent")}
    print(f"[sandbox] invoice sent: {invoice_id}")

    print("\n=== SANDBOX VERIFICATION RESULT (paste into SANDBOX-VERIFICATION.md) ===")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
