"""Config schema tests: loading, defaults, quoting, deposits."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from paypal_frontdesk.config import Business, money

from conftest import BUSINESS_DICT


def test_from_dict_loads_services_and_aliases(business):
    assert business.name == "Test Shine Detailing"
    assert business.agent_name == "Riley"
    assert len(business.services) == 3
    interior = business.services[0]
    assert interior.price_from == Decimal("120")
    assert "interior" in interior.aliases


def test_defaults_applied():
    raw = {
        "name": "B",
        "services": [{"name": "Cut", "price_from": 50, "duration_min": 30}],
    }
    biz = Business.from_dict(raw)
    assert biz.currency == "USD"
    assert biz.deposit_percent == 25
    assert biz.agent_name == "Riley"


def test_from_json_roundtrip(tmp_path):
    path = tmp_path / "biz.json"
    path.write_text(json.dumps(BUSINESS_DICT), encoding="utf-8")
    biz = Business.from_json(path)
    assert biz.name == "Test Shine Detailing"
    assert biz.services[2].deposit_flat == Decimal("15")


def test_empty_services_rejected():
    with pytest.raises(ValueError):
        Business.from_dict({"name": "B", "services": []})


def test_bad_deposit_percent_rejected():
    raw = dict(BUSINESS_DICT, deposit_percent=0)
    with pytest.raises(ValueError):
        Business.from_dict(raw)


def test_quote_deposit_from_percent(business):
    quote = business.quote(business.services[0])  # $120 @ 25%
    assert money(quote.price) == "120.00"
    assert money(quote.deposit) == "30.00"
    assert money(quote.balance) == "90.00"
    assert quote.currency == "USD"


def test_quote_deposit_flat_overrides_percent(business):
    quote = business.quote(business.services[2])  # flat $15 deposit
    assert money(quote.deposit) == "15.00"
    assert money(quote.balance) == "45.00"


def test_find_service_by_alias_and_name(business):
    assert business.find_service("interior").name == "Full Interior Detail"
    assert business.find_service("Exterior Wash & Wax").name == "Exterior Wash & Wax"
    assert business.find_service("plumbing") is None


def test_money_formatting():
    assert money(Decimal("30")) == "30.00"
    assert money(Decimal("29.999")) == "30.00"
