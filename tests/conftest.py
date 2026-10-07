"""Shared fixtures: a test business and an ephemeral mock PayPal server."""

from __future__ import annotations

import urllib.request
from datetime import date

import pytest

from paypal_frontdesk.agent import FrontDeskAgent
from paypal_frontdesk.config import Business
from paypal_frontdesk.llm import RulesExtractor
from paypal_frontdesk.paypal_client import PayPalClient, find_link
from paypal_frontdesk.paypal_mock import MockPayPalServer

TODAY = date(2026, 10, 7)  # a Wednesday — keeps date parsing deterministic

BUSINESS_DICT = {
    "name": "Test Shine Detailing",
    "agent_name": "Riley",
    "currency": "USD",
    "deposit_percent": 25,
    "services": [
        {
            "name": "Full Interior Detail",
            "price_from": 120,
            "duration_min": 120,
            "aliases": ["interior", "interior detail", "full interior"],
        },
        {
            "name": "Exterior Wash & Wax",
            "price_from": 80,
            "duration_min": 90,
            "aliases": ["exterior", "wash", "wax"],
        },
        {
            "name": "Engine Bay Cleaning",
            "price_from": 60,
            "duration_min": 45,
            "aliases": ["engine", "engine bay"],
            "deposit_flat": 15,
        },
    ],
}


@pytest.fixture()
def business() -> Business:
    return Business.from_dict(BUSINESS_DICT)


@pytest.fixture()
def mock_paypal():
    server = MockPayPalServer().start()
    yield server
    server.stop()


@pytest.fixture()
def client(mock_paypal) -> PayPalClient:
    return PayPalClient(
        base_url=mock_paypal.base_url, client_id="test-id", client_secret="test-secret"
    )


@pytest.fixture()
def agent(business, client) -> FrontDeskAgent:
    extractor = RulesExtractor(business.services, TODAY)
    return FrontDeskAgent(
        business,
        client,
        extractor=extractor,
        today=TODAY,
        extractor_description="rules-based extraction (test)",
    )


def approve_order(client: PayPalClient, order: dict) -> bool:
    """Simulate the buyer opening the PayPal approval link."""
    link = find_link(order, "approve")
    assert link, "order has no approve link"
    with urllib.request.urlopen(link, timeout=5) as resp:
        return 200 <= resp.status < 300


def book_and_pay(agent: FrontDeskAgent):
    """Drive the agent through a full paid booking; returns the booking."""
    agent.greeting()
    agent.handle(
        "Hi, I'm Dana Lee. I'd like a full interior detail next Friday at 2 pm. "
        "My number is 555-214-8690 and my email is dana.lee@example.com."
    )
    agent.handle("yes")   # confirm details -> quote
    agent.handle("yes")   # accept quote -> order created
    assert agent.approve_current_order()
    agent.handle("I just paid")
    booking = agent.current_booking
    assert booking is not None and booking.status == "BOOKED"
    return booking
