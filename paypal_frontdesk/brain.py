"""Dialogue brain: a stateful intake machine with payment at its core.

Adapted from a voice-intake pattern, re-centered on money: collecting the
customer's details is only the road to a deposit. The brain never talks to
the network — it emits explicit payment *actions* (``create_order``,
``check_payment``, ``refund``, ``create_invoice``) that the orchestrator in
``agent.py`` executes against the PayPal client, then reports back through
the ``on_*`` callbacks. Every transition around payment is therefore
explicit and independently testable:

    QUOTED --yes--> create_order --> AWAITING_DEPOSIT
    AWAITING_DEPOSIT --capture ok--> BOOKED
    AWAITING_DEPOSIT --expired--> create_order (fresh link)
    BOOKED --cancel+yes--> refund --> CLOSED
    BOOKED --invoice--> create_invoice --> BOOKED
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from enum import Enum, auto
from typing import Callable

from .config import Business, Quote

# An extractor maps one customer message to any fields it can find.
Extractor = Callable[[str], dict[str, str]]


class State(Enum):
    COLLECTING = auto()
    CONFIRMING = auto()
    QUOTED = auto()
    AWAITING_DEPOSIT = auto()
    BOOKED = auto()
    CANCEL_CONFIRM = auto()
    CLOSED = auto()


REQUIRED_SLOTS = ("name", "service", "date", "time")

_SLOT_PROMPTS = {
    "name": "May I have your name?",
    "service": "Which service would you like? We offer {menu}.",
    "date": "What day works best for you?",
    "time": "And what time of day?",
}


@dataclass
class Lead:
    name: str | None = None
    phone: str | None = None
    email: str | None = None
    service: str | None = None
    date: str | None = None  # ISO YYYY-MM-DD
    time: str | None = None  # 24h HH:MM

    @property
    def contact(self) -> str | None:
        return self.email or self.phone

    def missing(self) -> list[str]:
        slots = [f for f in REQUIRED_SLOTS if getattr(self, f) in (None, "")]
        if not slots and self.contact is None:
            slots.append("contact")
        return slots

    def summary(self) -> str:
        parts = [
            f"Name: {self.name}",
            f"Service: {self.service}",
            f"Date: {self.date}",
            f"Time: {self.time}",
        ]
        if self.phone:
            parts.append(f"Phone: {self.phone}")
        if self.email:
            parts.append(f"Email: {self.email}")
        return "; ".join(parts)


class DialogueBrain:
    """Drives one customer conversation from greeting to paid booking."""

    def __init__(
        self,
        business: Business,
        extractor: Extractor,
        today: date | None = None,
    ) -> None:
        self.business = business
        self.today = today or date.today()
        self._extract = extractor
        self.state = State.COLLECTING
        self.lead = Lead()
        self.quote: Quote | None = None
        self.history: list[tuple[str, str]] = []  # (speaker, text)
        self._actions: list[str] = []
        self._greeting: str | None = None

    # -- public API ---------------------------------------------------------

    @property
    def done(self) -> bool:
        return self.state is State.CLOSED

    def greeting(self) -> str:
        if self._greeting is None:
            self._greeting = (
                f"Hi, thanks for reaching out to {self.business.name} — this is "
                f"{self.business.agent_name}. I can book your appointment and take "
                f"the deposit right here in the chat. What can I do for you?"
            )
            self.history.append(("agent", self._greeting))
        return self._greeting

    def handle(self, message: str) -> str:
        """Process one customer turn and return the agent's reply."""
        message = message.strip()
        if not message:
            reply = "Sorry, I didn't catch that. Could you say it again?"
            self.history.append(("agent", reply))
            return reply
        self.history.append(("customer", message))

        handler = {
            State.COLLECTING: self._handle_collecting,
            State.CONFIRMING: self._handle_confirming,
            State.QUOTED: self._handle_quoted,
            State.AWAITING_DEPOSIT: self._handle_awaiting_deposit,
            State.BOOKED: self._handle_booked,
            State.CANCEL_CONFIRM: self._handle_cancel_confirm,
            State.CLOSED: self._handle_closed,
        }[self.state]
        reply = handler(message)

        self.history.append(("agent", reply))
        return reply

    def take_action(self) -> str | None:
        """Pop the next payment action the orchestrator should execute."""
        return self._actions.pop(0) if self._actions else None

    # -- payment callbacks (invoked by the orchestrator) --------------------

    def on_capture_success(self, capture_id: str, deposit: str, currency: str) -> str:
        self.state = State.BOOKED
        reply = (
            f"Payment captured — {deposit} {currency} deposit received "
            f"(capture id {capture_id}). You're booked, {self.lead.name}: "
            f"{self.lead.service} on {self.lead.date} at {self.lead.time}. "
            f"I can also cancel with a full refund, or send the balance "
            f"invoice after your visit."
        )
        self.history.append(("agent", reply))
        return reply

    def on_capture_pending(self) -> str:
        reply = (
            "I don't see the payment approved yet. Please open the secure "
            "PayPal link I sent, approve the deposit, and then let me know."
        )
        self.history.append(("agent", reply))
        return reply

    def on_capture_failed(self, reason: str) -> str:
        reply = (
            f"The deposit payment could not be captured ({reason}). "
            "Your booking is not confirmed yet — say the word and I'll try "
            "the payment again."
        )
        self.history.append(("agent", reply))
        return reply

    def on_order_expired(self) -> str:
        # Queue a fresh order; the orchestrator will send the new link.
        self._actions.append("create_order")
        reply = (
            "That checkout link expired before payment. No charge was made — "
            "I'm generating a fresh PayPal link for you now."
        )
        self.history.append(("agent", reply))
        return reply

    def on_refund_success(self, refund_id: str, amount: str, currency: str) -> str:
        self.state = State.CLOSED
        reply = (
            f"Done — your booking is cancelled and the {amount} {currency} "
            f"deposit has been refunded in full (refund id {refund_id}). "
            "Sorry to see you go; we're here whenever you need us."
        )
        self.history.append(("agent", reply))
        return reply

    def on_refund_failed(self, reason: str) -> str:
        self.state = State.BOOKED
        reply = (
            f"I couldn't process the refund ({reason}). Your booking is "
            "still active and no money has moved — shall I try again?"
        )
        self.history.append(("agent", reply))
        return reply

    def on_invoice_sent(self, invoice_id: str, amount: str, currency: str) -> str:
        reply = (
            f"Invoice {invoice_id} for {amount} {currency} has been sent to "
            f"{self.lead.contact}. It's payable online through PayPal."
        )
        self.history.append(("agent", reply))
        return reply

    def on_invoice_failed(self, reason: str) -> str:
        reply = f"I couldn't send the invoice ({reason}). Want me to try again?"
        self.history.append(("agent", reply))
        return reply

    # -- state handlers -----------------------------------------------------

    def _handle_collecting(self, message: str) -> str:
        found = self._safe_extract(message)
        if found.get("intent") == "cancel" and not any(
            getattr(self.lead, f) for f in REQUIRED_SLOTS
        ):
            self.state = State.CLOSED
            return "No problem — I've scrapped that. Anything else I can help with?"
        self._fill_slots(found, message)
        return self._advance()

    def _handle_confirming(self, message: str) -> str:
        found = self._safe_extract(message)
        intent = found.get("intent")
        if intent == "deny":
            changed = self._apply_corrections(found)
            if changed:
                return "Updated. " + self.lead.summary() + ". Is everything correct now?"
            field_m = re.search(
                r"\b(name|phone|email|service|date|day|time)\b", message, re.I
            )
            if field_m:
                which = {"day": "date", "number": "phone"}.get(
                    field_m.group(1).lower(), field_m.group(1).lower()
                )
                if which in ("name", "phone", "email", "service", "date", "time"):
                    setattr(self.lead, which, None)
                    self.state = State.COLLECTING
                    return self._prompt_for(which)
            return (
                "No problem. What should I change — your name, service, "
                "date, time, or contact details?"
            )
        if intent == "confirm":
            service = self.business.find_service(self.lead.service or "")
            if service is None:
                self.state = State.COLLECTING
                self.lead.service = None
                return self._prompt_for("service")
            self.quote = self.business.quote(service)
            self.state = State.QUOTED
            return (
                f"Here's your quote: {self.quote.describe()}. The deposit "
                "is paid securely through PayPal and fully refundable on "
                "cancellation. Shall I create your PayPal checkout link?"
            )
        changed = self._apply_corrections(found)
        if changed:
            return "Updated. " + self.lead.summary() + ". Is everything correct now?"
        return "Sorry — was that a yes or a no?"

    def _handle_quoted(self, message: str) -> str:
        found = self._safe_extract(message)
        intent = found.get("intent")
        if intent == "confirm":
            self.state = State.AWAITING_DEPOSIT
            self._actions.append("create_order")
            return "Great — setting up your secure PayPal checkout now."
        if intent == "deny":
            self.state = State.COLLECTING
            return (
                "No problem, nothing has been charged. What would you like "
                "to change — the service, date, or time?"
            )
        if intent == "cancel":
            self.state = State.CLOSED
            return "All set — no booking was made and nothing was charged."
        return (
            "Just say 'yes' and I'll create the PayPal link for the "
            "deposit, or 'no' to change the booking."
        )

    def _handle_awaiting_deposit(self, message: str) -> str:
        found = self._safe_extract(message)
        if found.get("intent") in ("paid", "confirm"):
            self._actions.append("check_payment")
            return "Let me check that payment with PayPal…"
        if found.get("intent") == "cancel":
            self.state = State.CLOSED
            return (
                "Understood — the unpaid booking request is cancelled. The "
                "PayPal checkout was never charged, so there's nothing to refund."
            )
        return (
            "Once you've approved the deposit at the PayPal link, just say "
            "'paid' and I'll confirm your booking instantly."
        )

    def _handle_booked(self, message: str) -> str:
        found = self._safe_extract(message)
        intent = found.get("intent")
        if intent == "cancel":
            self.state = State.CANCEL_CONFIRM
            return (
                "I can cancel that for you. Your deposit will be refunded "
                "in full to the original payment method. Are you sure you "
                "want to cancel?"
            )
        if intent == "invoice":
            self._actions.append("create_invoice")
            return "Of course — I'll send the balance invoice right now."
        return (
            f"You're booked for {self.lead.service} on {self.lead.date} at "
            f"{self.lead.time}. I can cancel with a full refund, or email "
            "you the balance invoice — just ask."
        )

    def _handle_cancel_confirm(self, message: str) -> str:
        found = self._safe_extract(message)
        intent = found.get("intent")
        if intent == "confirm":
            self._actions.append("refund")
            return "Cancelling your booking and refunding your deposit now…"
        if intent == "deny":
            self.state = State.BOOKED
            return "No changes made — your booking and deposit are safe."
        return "Was that a yes (cancel and refund) or a no (keep my booking)?"

    def _handle_closed(self, message: str) -> str:
        found = self._safe_extract(message)
        if found.get("intent") == "book" and found.get("service"):
            # A returning customer starts a brand-new conversation.
            self.state = State.COLLECTING
            self.lead = Lead()
            self.quote = None
            self._fill_slots(found, message)
            return "Happy to set up a new booking. " + self._advance()
        return "Is there anything else I can help you with?"

    # -- slot machinery -----------------------------------------------------

    def _safe_extract(self, message: str) -> dict[str, str]:
        try:
            return self._extract(message) or {}
        except Exception:
            return {}

    def _fill_slots(self, found: dict[str, str], message: str) -> None:
        for key in ("name", "phone", "email", "service", "date", "time"):
            if key in found and getattr(self.lead, key) in (None, ""):
                if key == "service":
                    svc = self.business.find_service(found[key])
                    if svc is None:
                        continue
                    setattr(self.lead, key, svc.name)
                else:
                    setattr(self.lead, key, found[key])
        # A bare "Dana Lee" in answer to "May I have your name?"
        if self.lead.name is None and not found.get("name"):
            candidate = message.strip().strip(".,!")
            words = candidate.split()
            if 1 <= len(words) <= 3 and all(
                re.fullmatch(r"[A-Za-z'\-]+", w) for w in words
            ):
                self.lead.name = " ".join(w.capitalize() for w in words)

    def _apply_corrections(self, found: dict[str, str]) -> bool:
        changed = False
        for key in ("name", "phone", "email", "service", "date", "time"):
            if key in found:
                if key == "service":
                    svc = self.business.find_service(found[key])
                    if svc is None:
                        continue
                    self.lead.service = svc.name
                else:
                    setattr(self.lead, key, found[key])
                changed = True
        return changed

    def _prompt_for(self, slot: str) -> str:
        if slot == "contact":
            return (
                f"Thanks {self.lead.name}. What's the best phone number or "
                "email to reach you? The receipt and invoice go there."
            )
        return _SLOT_PROMPTS[slot].format(menu=self.business.service_menu())

    def _advance(self) -> str:
        missing = self.lead.missing()
        if missing:
            return self._prompt_for(missing[0])
        self.state = State.CONFIRMING
        return (
            "Let me confirm what I have. " + self.lead.summary()
            + ". Did I get everything right?"
        )
