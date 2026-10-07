"""CLI tests: the scripted demo must run end-to-end and print every id."""

from __future__ import annotations

from paypal_frontdesk.cli import build_agent, main, run_demo

from conftest import BUSINESS_DICT
from paypal_frontdesk.config import Business


def test_demo_script_end_to_end(capsys):
    business = Business.from_dict(BUSINESS_DICT)
    agent, mock = build_agent(business, use_mock=True)
    try:
        assert run_demo(agent) == 0
    finally:
        mock.stop()
    out = capsys.readouterr().out
    assert "ORDER-" in out       # order created
    assert "APPROVED" in out     # customer opened the approval link
    assert "CAPTURE-" in out     # deposit captured
    assert "INV-" in out         # balance invoice sent
    assert "REFUND-" in out      # cancellation refunded
    assert "rules-based" in out  # extraction mode disclosed
    assert "PAYMENT TRAIL" in out


def test_main_demo_flag(capsys):
    rc = main(["--demo"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "ORDER-" in out and "REFUND-" in out and "INV-" in out


def test_cli_demo_uses_bundled_business_json():
    from paypal_frontdesk.cli import DEFAULT_BUSINESS_JSON

    assert DEFAULT_BUSINESS_JSON.exists()
    business = Business.from_json(DEFAULT_BUSINESS_JSON)
    assert business.services  # valid catalog
