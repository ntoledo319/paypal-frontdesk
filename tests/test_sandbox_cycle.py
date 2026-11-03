"""Verification-runner preflight must reject unsafe configuration before I/O."""

import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "sandbox_cycle", Path(__file__).resolve().parents[1] / "scripts/sandbox_cycle.py"
)
cycle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cycle)


@pytest.mark.parametrize("base_url", [
    "https://api-m.paypal.com",
    "http://api-m.sandbox.paypal.com",
    "https://api-m.sandbox.paypal.com.attacker.invalid",
])
def test_rejects_non_sandbox_before_client_creation(monkeypatch, base_url):
    monkeypatch.setenv("PAYPAL_BASE_URL", base_url)
    monkeypatch.setattr(cycle.PayPalClient, "from_env", lambda: pytest.fail("client created"))
    with pytest.raises(SystemExit) as error:
        cycle.main(["--buyer-email", "sb-test@personal.example.net"])
    assert error.value.code == 2


@pytest.mark.parametrize("args", [[], ["--buyer-email", "not-email"],
                                  ["--buyer-email", "buyer@@personal.example.com"]])
def test_requires_real_buyer_argument_before_client_creation(monkeypatch, args):
    monkeypatch.setattr(cycle.PayPalClient, "from_env", lambda: pytest.fail("client created"))
    with pytest.raises(SystemExit) as error:
        cycle.main(args)
    assert error.value.code == 2


@pytest.mark.parametrize("buyer_email", [
    "sb-test@personal.example.net", "sb-generated@personal.example.com",
])
def test_offline_runner_journey_uses_selected_buyer(monkeypatch, client, mock_paypal, capsys, buyer_email):
    monkeypatch.setenv("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com")
    monkeypatch.setattr(cycle.PayPalClient, "from_env", lambda: client)
    from conftest import approve_order

    def approve(_prompt):
        order = next(iter(mock_paypal.orders.values()))
        assert approve_order(client, order)
        return ""

    monkeypatch.setattr("builtins.input", approve)
    original = client.create_invoice
    recipients = []

    def invoice(*args, **kwargs):
        recipients.append(kwargs["recipient_email"])
        return original(*args, **kwargs)

    monkeypatch.setattr(client, "create_invoice", invoice)
    assert cycle.main(["--buyer-email", buyer_email]) == 0
    assert recipients == [buyer_email]
    output = capsys.readouterr().out
    assert "CAPTURE-" in output and "REFUND-" in output and "INV-" in output
