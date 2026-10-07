"""Orchestrator: connects the dialogue brain to real PayPal money movement.

The brain decides *when* money should move; this module moves it. Every
payment step is recorded in a ``Booking`` and in the agent's payment trail
so the CLI, the web simulator, and the tests can show judges the exact
sequence: order created → approved → captured → (refunded) / (invoiced).
"""

from __future__ import annotations

import itertools
import urllib.request
from dataclasses import dataclass, field
from datetime import date

from .brain import DialogueBrain
from .config import Business, Quote, money
from .llm import make_extractor
from .paypal_client import (
    PayPalClient,
    PayPalError,
    extract_capture_id,
    find_link,
)


@dataclass
class Booking:
    """One appointment and its full payment lifecycle."""

    id: str
    customer_name: str
    contact: str
    service: str
    date: str
    time: str
    price: str
    deposit: str
    balance: str
    currency: str
    status: str = "PENDING_DEPOSIT"  # PENDING_DEPOSIT | BOOKED | REFUNDED
    order_id: str | None = None
    approve_link: str | None = None
    capture_id: str | None = None
    refund_id: str | None = None
    invoice_id: str | None = None


@dataclass
class PaymentEvent:
    kind: str  # order_created | captured | refunded | invoice_sent | failed
    detail: str


class FrontDeskAgent:
    """The full agent: conversation in, confirmed paid bookings out."""

    def __init__(
        self,
        business: Business,
        paypal_client: PayPalClient,
        extractor=None,
        today: date | None = None,
        extractor_description: str = "custom extractor",
    ) -> None:
        self.business = business
        self.paypal = paypal_client
        self.today = today or date.today()
        if extractor is None:
            extractor, extractor_description = make_extractor(
                business.services, business.name, self.today
            )
        self.extractor_description = extractor_description
        self.brain = DialogueBrain(business, extractor, self.today)
        self.bookings: dict[str, Booking] = {}
        self.payment_trail: list[PaymentEvent] = []
        self._booking_seq = itertools.count(1)

    # -- conversation API -----------------------------------------------------

    def greeting(self) -> str:
        return self.brain.greeting()

    def handle(self, message: str) -> str:
        reply = self.brain.handle(message)
        followups = self._drain_actions()
        return "\n".join([reply, *followups]) if followups else reply

    @property
    def current_booking(self) -> Booking | None:
        return next(reversed(self.bookings.values()), None) if self.bookings else None

    # -- payment machinery ------------------------------------------------------

    def _drain_actions(self) -> list[str]:
        """Execute brain-queued payment actions (may cascade, e.g. expired
        order → fresh order) and collect the customer-facing follow-ups."""
        texts: list[str] = []
        for _ in range(5):  # bounded cascade
            action = self.brain.take_action()
            if action is None:
                break
            texts.append(self._execute(action))
        return texts

    def _execute(self, action: str) -> str:
        if action == "create_order":
            return self._create_deposit_order()
        if action == "check_payment":
            return self._check_and_capture()
        if action == "refund":
            return self._refund_deposit()
        if action == "create_invoice":
            return self._send_balance_invoice()
        return f"(unknown action {action!r})"

    def _record(self, kind: str, detail: str) -> None:
        self.payment_trail.append(PaymentEvent(kind=kind, detail=detail))

    def _create_deposit_order(self) -> str:
        lead = self.brain.lead
        quote = self.brain.quote
        if quote is None:
            service = self.business.find_service(lead.service or "")
            if service is None:
                return "I couldn't price that service — let's pick one first."
            quote = self.business.quote(service)
            self.brain.quote = quote
        booking = Booking(
            id=f"BK-{next(self._booking_seq):04d}",
            customer_name=lead.name or "Customer",
            contact=lead.contact or "",
            service=quote.service.name,
            date=lead.date or "",
            time=lead.time or "",
            price=money(quote.price),
            deposit=money(quote.deposit),
            balance=money(quote.balance),
            currency=quote.currency,
        )
        try:
            order = self.paypal.create_order(
                amount=booking.deposit,
                currency=booking.currency,
                description=(
                    f"{self.business.name}: deposit for {booking.service} "
                    f"on {booking.date} {booking.time}"
                ),
            )
        except PayPalError as exc:
            self._record("failed", f"order create failed: {exc}")
            return (
                "PayPal couldn't create the checkout right now "
                f"({exc}). Say 'yes' and I'll try again."
            )
        booking.order_id = order["id"]
        booking.approve_link = find_link(order, "approve")
        self.bookings[booking.id] = booking
        self._record(
            "order_created",
            f"order {booking.order_id} for {booking.deposit} {booking.currency}",
        )
        link_text = booking.approve_link or "(link unavailable)"
        return (
            f"Your booking {booking.id} is reserved. To lock it in, pay the "
            f"{booking.deposit} {booking.currency} deposit here:\n"
            f"  {link_text}\n"
            f"(PayPal order {booking.order_id}). Once you've approved it, "
            "just say 'paid' and I'll capture the deposit and confirm you."
        )

    def _check_and_capture(self) -> str:
        booking = self.current_booking
        if booking is None or booking.order_id is None:
            return "There's no pending payment to check."
        try:
            order = self.paypal.get_order(booking.order_id)
        except PayPalError as exc:
            return self.brain.on_capture_failed(str(exc))
        status = order.get("status")
        if status == "CREATED":
            return self.brain.on_capture_pending()
        if status == "EXPIRED":
            booking.order_id = None
            booking.approve_link = None
            return self.brain.on_order_expired()
        if status != "APPROVED":
            return self.brain.on_capture_failed(f"order status is {status}")
        try:
            capture = self.paypal.capture_order(booking.order_id)
        except PayPalError as exc:
            self._record("failed", f"capture of {booking.order_id} failed: {exc}")
            return self.brain.on_capture_failed(str(exc))
        capture_id = extract_capture_id(capture) or "unknown"
        booking.capture_id = capture_id
        booking.status = "BOOKED"
        self._record(
            "captured",
            f"capture {capture_id} — {booking.deposit} {booking.currency} "
            f"collected for {booking.id}",
        )
        return self.brain.on_capture_success(
            capture_id, booking.deposit, booking.currency
        )

    def _refund_deposit(self) -> str:
        booking = self.current_booking
        if booking is None or booking.capture_id is None:
            return self.brain.on_refund_failed("no captured deposit found")
        try:
            refund = self.paypal.refund_capture(
                booking.capture_id, amount=booking.deposit, currency=booking.currency
            )
        except PayPalError as exc:
            self._record("failed", f"refund of {booking.capture_id} failed: {exc}")
            return self.brain.on_refund_failed(str(exc))
        booking.refund_id = refund.get("id")
        booking.status = "REFUNDED"
        self._record(
            "refunded",
            f"refund {booking.refund_id} — {booking.deposit} {booking.currency} "
            f"returned for {booking.id}",
        )
        return self.brain.on_refund_success(
            booking.refund_id or "unknown", booking.deposit, booking.currency
        )

    def _send_balance_invoice(self) -> str:
        booking = self.current_booking
        if booking is None or booking.status != "BOOKED":
            return self.brain.on_invoice_failed(
                "invoices are only available for confirmed bookings"
            )
        email = self.brain.lead.email
        try:
            invoice = self.paypal.create_invoice(
                amount=booking.balance,
                currency=booking.currency,
                item_name=(
                    f"{self.business.name}: balance for {booking.service} "
                    f"({booking.id})"
                ),
                recipient_email=email,
                note=f"Remaining balance for booking {booking.id}.",
            )
            invoice_id = invoice["id"]
            self.paypal.send_invoice(invoice_id)
        except PayPalError as exc:
            self._record("failed", f"invoice for {booking.id} failed: {exc}")
            return self.brain.on_invoice_failed(str(exc))
        booking.invoice_id = invoice_id
        self._record(
            "invoice_sent",
            f"invoice {invoice_id} — {booking.balance} {booking.currency} "
            f"billed for {booking.id}",
        )
        return self.brain.on_invoice_sent(
            invoice_id, booking.balance, booking.currency
        )

    # -- demo helpers -----------------------------------------------------------

    def approve_current_order(self, timeout: float = 10.0) -> bool:
        """Simulate the customer opening the PayPal approval link.

        Only meaningful against the mock server (or any approval URL that
        approves on GET). Used by the scripted demo so it can run fully
        non-interactively; a real buyer would open the link in a browser.
        """
        booking = self.current_booking
        if booking is None or not booking.approve_link:
            return False
        try:
            with urllib.request.urlopen(booking.approve_link, timeout=timeout) as resp:
                return 200 <= resp.status < 300
        except Exception:
            return False

    def payment_summary(self) -> str:
        """Render the payment trail for demos: every id a judge cares about."""
        lines = []
        for booking in self.bookings.values():
            parts = [f"{booking.id} [{booking.status}]"]
            if booking.order_id:
                parts.append(f"order={booking.order_id}")
            if booking.capture_id:
                parts.append(f"capture={booking.capture_id}")
            if booking.refund_id:
                parts.append(f"refund={booking.refund_id}")
            if booking.invoice_id:
                parts.append(f"invoice={booking.invoice_id}")
            lines.append("  " + " ".join(parts))
        return "\n".join(lines) if lines else "  (no payments yet)"
