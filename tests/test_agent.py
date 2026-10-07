"""End-to-end agent tests: book+pay, cancel+refund, invoice, error paths."""

from __future__ import annotations

from paypal_frontdesk.brain import State

from conftest import book_and_pay


def test_full_book_and_pay_flow(agent):
    booking = book_and_pay(agent)
    assert booking.order_id.startswith("ORDER-")
    assert booking.capture_id.startswith("CAPTURE-")
    assert booking.deposit == "30.00"          # 25% of $120
    assert booking.balance == "90.00"
    assert booking.service == "Full Interior Detail"
    kinds = [e.kind for e in agent.payment_trail]
    assert kinds == ["order_created", "captured"]


def test_booking_confirmation_mentions_capture_id(agent):
    book_and_pay(agent)
    confirmation = agent.brain.history[-1][1]
    assert "CAPTURE-" in confirmation
    assert "booked" in confirmation.lower()


def test_payment_pending_until_approved(agent):
    agent.greeting()
    agent.handle("I'm Dana Lee, interior detail next Friday at 2 pm, dana@x.com")
    agent.handle("yes")
    agent.handle("yes")  # order created, not yet approved
    reply = agent.handle("I just paid")
    assert "don't see the payment approved yet" in reply
    booking = agent.current_booking
    assert booking.status == "PENDING_DEPOSIT"
    assert booking.capture_id is None


def test_capture_failure_keeps_booking_unconfirmed(agent, mock_paypal):
    agent.greeting()
    agent.handle("I'm Dana Lee, interior detail next Friday at 2 pm, dana@x.com")
    agent.handle("yes")
    agent.handle("yes")
    assert agent.approve_current_order()
    mock_paypal.fail_next_capture = True
    reply = agent.handle("I just paid")
    assert "could not be captured" in reply
    booking = agent.current_booking
    assert booking.status == "PENDING_DEPOSIT"
    assert agent.brain.state is State.AWAITING_DEPOSIT
    # Retry succeeds because the mock failure is one-shot.
    reply = agent.handle("I just paid")
    assert "booked" in reply.lower()
    assert booking.capture_id.startswith("CAPTURE-")


def test_expired_order_triggers_fresh_link(agent, mock_paypal):
    agent.greeting()
    agent.handle("I'm Dana Lee, interior detail next Friday at 2 pm, dana@x.com")
    agent.handle("yes")
    agent.handle("yes")
    booking = agent.current_booking
    old_order = booking.order_id
    mock_paypal.expire_order(old_order)
    reply = agent.handle("I just paid")
    assert "expired" in reply.lower()
    # A fresh order + link was generated in the same turn (cascade).
    fresh = agent.current_booking
    assert fresh.order_id is not None
    assert fresh.order_id != old_order
    assert fresh.approve_link
    assert agent.brain.state is State.AWAITING_DEPOSIT


def test_cancel_and_full_refund(agent):
    booking = book_and_pay(agent)
    reply = agent.handle("Actually, I need to cancel my appointment")
    assert "refunded in full" in reply
    reply = agent.handle("yes")
    assert "REFUND-" in reply
    assert booking.status == "REFUNDED"
    assert booking.refund_id.startswith("REFUND-")
    assert agent.brain.state is State.CLOSED
    kinds = [e.kind for e in agent.payment_trail]
    assert kinds == ["order_created", "captured", "refunded"]


def test_cancel_declined_keeps_booking(agent):
    booking = book_and_pay(agent)
    agent.handle("cancel my appointment")
    reply = agent.handle("no")
    assert "no changes made" in reply.lower()
    assert booking.status == "BOOKED"
    assert booking.refund_id is None


def test_balance_invoice_sent(agent):
    booking = book_and_pay(agent)
    reply = agent.handle("Can you email me the invoice for the balance?")
    assert "INV-" in reply
    assert "90.00" in reply  # balance after 25% deposit on $120
    assert "dana.lee@example.com" in reply
    assert booking.invoice_id.startswith("INV-")
    kinds = [e.kind for e in agent.payment_trail]
    assert kinds == ["order_created", "captured", "invoice_sent"]


def test_invoice_requires_confirmed_booking(agent):
    agent.greeting()
    reply = agent.handle("send me an invoice")
    assert "INV-" not in reply


def test_full_journey_all_four_ids(agent):
    booking = book_and_pay(agent)
    agent.handle("email me the invoice for the balance")
    agent.handle("cancel my appointment")
    agent.handle("yes")
    assert booking.order_id.startswith("ORDER-")
    assert booking.capture_id.startswith("CAPTURE-")
    assert booking.invoice_id.startswith("INV-")
    assert booking.refund_id.startswith("REFUND-")
    kinds = [e.kind for e in agent.payment_trail]
    assert kinds == ["order_created", "captured", "invoice_sent", "refunded"]


def test_agent_reports_extraction_mode(agent):
    assert "rules-based" in agent.extractor_description


def test_payment_summary_lists_ids(agent):
    book_and_pay(agent)
    summary = agent.payment_summary()
    assert "ORDER-" in summary
    assert "CAPTURE-" in summary
    assert "BOOKED" in summary
