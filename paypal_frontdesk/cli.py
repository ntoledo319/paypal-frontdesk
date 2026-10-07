"""Interactive / scripted demo CLI.

    python -m paypal_frontdesk.cli --mock            # interactive, offline
    python -m paypal_frontdesk.cli --mock --demo     # scripted, non-interactive
    python -m paypal_frontdesk.cli                   # live PayPal sandbox
                                                     # (env credentials required)

Mock mode spins up the bundled mock PayPal server on an ephemeral port, so
the entire agentic-commerce loop — order, approval, capture, refund,
invoice — runs with zero network access and zero credentials.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .agent import FrontDeskAgent
from .config import Business
from .paypal_client import PayPalClient
from .paypal_mock import MockPayPalServer

DEFAULT_BUSINESS_JSON = Path(__file__).resolve().parent.parent / "business.json"

# A complete scripted journey: book + pay, invoice the balance, cancel +
# refund. Deterministic under the rules-based extractor.
DEMO_TURNS = [
    "Hi, I'm Dana Lee. I'd like a full interior detail next Friday at 2 pm. "
    "My number is 555-214-8690 and my email is dana.lee@example.com.",
    "yes",
    "yes",
    "I just paid",
    "Can you email me the invoice for the balance?",
    "Actually, I need to cancel my appointment",
    "yes",
]


def build_agent(business: Business, use_mock: bool) -> tuple[FrontDeskAgent, MockPayPalServer | None]:
    if use_mock:
        mock = MockPayPalServer().start()
        client = PayPalClient(
            base_url=mock.base_url, client_id="mock-id", client_secret="mock-secret"
        )
        return FrontDeskAgent(business, client), mock
    client = PayPalClient.from_env()
    return FrontDeskAgent(business, client), None


def _print_agent(text: str) -> None:
    for line in text.splitlines():
        print(f"  Agent: {line}")


def run_demo(agent: FrontDeskAgent, out=sys.stdout) -> int:
    """Run the scripted booking journey end-to-end, non-interactively."""
    print(f"[demo] extraction mode: {agent.extractor_description}")
    _print_agent(agent.greeting())
    for turn in DEMO_TURNS:
        print(f"  You:   {turn}")
        reply = agent.handle(turn)
        _print_agent(reply)
        booking = agent.current_booking
        if (
            booking is not None
            and booking.status == "PENDING_DEPOSIT"
            and booking.approve_link
        ):
            # Stand in for the customer clicking the PayPal link.
            ok = agent.approve_current_order()
            print(f"  [demo] customer opens PayPal approval link "
                  f"(order {booking.order_id}) -> {'APPROVED' if ok else 'FAILED'}")
    print("\n=== PAYMENT TRAIL ===")
    print(agent.payment_summary())
    return 0


def run_interactive(agent: FrontDeskAgent, mock: MockPayPalServer | None) -> int:
    print(f"[extraction mode: {agent.extractor_description}]")
    if mock is not None:
        print("[mock PayPal server at " + mock.base_url + " — type 'approve' "
              "to simulate opening the PayPal link]")
    print("[type 'quit' to exit]\n")
    _print_agent(agent.greeting())
    while True:
        try:
            message = input("  You:   ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if message.lower() in ("quit", "exit"):
            break
        if not message:
            continue
        if message.lower() == "approve" and mock is not None:
            ok = agent.approve_current_order()
            print("  [you open the PayPal link -> "
                  + ("APPROVED" if ok else "nothing to approve") + "]")
            continue
        _print_agent(agent.handle(message))
    print("\n=== PAYMENT TRAIL ===")
    print(agent.payment_summary())
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="paypal-frontdesk",
        description="AI front-desk agent that books appointments and collects "
        "deposits via PayPal.",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="run fully offline against the bundled mock PayPal server",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="run the scripted booking journey (non-interactive); implies --mock",
    )
    parser.add_argument(
        "--business",
        type=Path,
        default=DEFAULT_BUSINESS_JSON,
        help="path to business intake JSON (default: bundled business.json)",
    )
    args = parser.parse_args(argv)

    business = Business.from_json(args.business)
    use_mock = args.mock or args.demo
    agent, mock = build_agent(business, use_mock)
    try:
        if args.demo:
            return run_demo(agent)
        return run_interactive(agent, mock)
    finally:
        if mock is not None:
            mock.stop()


if __name__ == "__main__":
    raise SystemExit(main())
