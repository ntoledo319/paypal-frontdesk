"""Brain tests: dialogue transitions, slot filling, payment state callbacks."""

from __future__ import annotations

from paypal_frontdesk.brain import DialogueBrain, State
from paypal_frontdesk.llm import RulesExtractor

from conftest import TODAY


def make_brain(business) -> DialogueBrain:
    return DialogueBrain(business, RulesExtractor(business.services, TODAY), TODAY)


def test_greeting_mentions_business_and_deposit(business):
    brain = make_brain(business)
    text = brain.greeting()
    assert "Test Shine Detailing" in text
    assert "Riley" in text
    assert "deposit" in text
    assert brain.greeting() == text  # idempotent


def test_single_message_fills_all_slots_and_confirms(business):
    brain = make_brain(business)
    reply = brain.handle(
        "I'm Dana Lee, full interior detail next Friday at 2 pm, "
        "555-214-8690, dana.lee@example.com"
    )
    assert brain.state is State.CONFIRMING
    assert "Dana Lee" in reply
    assert "Full Interior Detail" in reply


def test_step_by_step_collection(business):
    brain = make_brain(business)
    brain.handle("Dana Lee")                       # bare name
    assert brain.lead.name == "Dana Lee"
    brain.handle("full interior")                  # service via alias
    assert brain.lead.service == "Full Interior Detail"
    brain.handle("next Friday")                    # date
    brain.handle("2 pm")                           # time
    reply = brain.handle("555-214-8690")           # contact -> confirm
    assert brain.state is State.CONFIRMING
    assert "did i get everything right" in reply.lower()


def test_asks_for_contact_after_slots(business):
    brain = make_brain(business)
    brain.handle("I'm Dana Lee, interior detail, next Friday at 2 pm")
    # All four slots filled but no contact yet -> still collecting contact.
    assert brain.state is State.COLLECTING
    assert "phone number or email" in brain.history[-1][1]


def test_confirm_yields_quote_with_deposit(business):
    brain = make_brain(business)
    brain.handle("I'm Dana Lee, interior detail next Friday at 2 pm, dana@x.com")
    reply = brain.handle("yes")
    assert brain.state is State.QUOTED
    assert "120.00" in reply
    assert "30.00" in reply  # 25% deposit
    assert "PayPal" in reply


def test_quote_accept_queues_create_order(business):
    brain = make_brain(business)
    brain.handle("I'm Dana Lee, interior detail next Friday at 2 pm, dana@x.com")
    brain.handle("yes")
    reply = brain.handle("yes")
    assert brain.state is State.AWAITING_DEPOSIT
    assert brain.take_action() == "create_order"
    assert brain.take_action() is None


def test_correction_at_confirm(business):
    brain = make_brain(business)
    brain.handle("I'm Dana Lee, interior detail next Friday at 2 pm, dana@x.com")
    reply = brain.handle("no, actually make it 3 pm")
    assert brain.lead.time == "15:00"
    assert "Updated" in reply
    assert brain.state is State.CONFIRMING


def test_cancel_before_payment_closes_without_charges(business):
    brain = make_brain(business)
    reply = brain.handle("cancel that please")
    assert brain.state is State.CLOSED
    assert brain.take_action() is None  # no payment action — nothing to refund


def test_unknown_utterance_reasks(business):
    brain = make_brain(business)
    brain.handle("I'm Dana Lee, interior detail next Friday at 2 pm, dana@x.com")
    reply = brain.handle("bananas")
    assert "yes or a no" in reply


def test_paid_message_queues_check_payment(business):
    brain = make_brain(business)
    brain.handle("I'm Dana Lee, interior detail next Friday at 2 pm, dana@x.com")
    brain.handle("yes")
    brain.handle("yes")
    brain.take_action()  # consume create_order
    reply = brain.handle("I just paid")
    assert brain.take_action() == "check_payment"
    assert "check" in reply.lower()


def test_on_capture_success_books(business):
    brain = make_brain(business)
    brain.handle("I'm Dana Lee, interior detail next Friday at 2 pm, dana@x.com")
    reply = brain.on_capture_success("CAPTURE-1", "30.00", "USD")
    assert brain.state is State.BOOKED
    assert "CAPTURE-1" in reply
    assert "booked" in reply.lower()


def test_on_order_expired_queues_fresh_order(business):
    brain = make_brain(business)
    reply = brain.on_order_expired()
    assert brain.take_action() == "create_order"
    assert "expired" in reply.lower()
    assert "No charge" in reply


def test_on_refund_success_closes(business):
    brain = make_brain(business)
    reply = brain.on_refund_success("REFUND-1", "30.00", "USD")
    assert brain.state is State.CLOSED
    assert "REFUND-1" in reply


def test_booked_cancel_requires_confirmation(business):
    brain = make_brain(business)
    brain.handle("I'm Dana Lee, interior detail next Friday at 2 pm, dana@x.com")
    brain.on_capture_success("CAPTURE-1", "30.00", "USD")
    reply = brain.handle("I need to cancel my appointment")
    assert brain.state is State.CANCEL_CONFIRM
    assert "refunded in full" in reply
    assert brain.take_action() is None  # not until they say yes
    brain.handle("yes")
    assert brain.take_action() == "refund"


def test_booked_invoice_request_queues_invoice(business):
    brain = make_brain(business)
    brain.handle("I'm Dana Lee, interior detail next Friday at 2 pm, dana@x.com")
    brain.on_capture_success("CAPTURE-1", "30.00", "USD")
    brain.handle("email me the invoice for the balance")
    assert brain.take_action() == "create_invoice"
