"""Business intake configuration: service catalog, pricing, and deposits.

The schema mirrors a small-business intake sheet: who the business is, what
it sells, and how much of each sale is collected up front as a deposit. All
money is handled as ``Decimal`` and rendered through :func:`money` so the
PayPal API always receives a correctly formatted string ("25.00").
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

_CENTS = Decimal("0.01")


def money(value: Decimal) -> str:
    """Render a Decimal as a PayPal-style amount string, e.g. '25.00'."""
    return str(value.quantize(_CENTS, rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class Service:
    name: str
    price_from: Decimal
    duration_min: int
    aliases: tuple[str, ...] = ()
    deposit_flat: Decimal | None = None  # overrides the business % when set


@dataclass(frozen=True)
class Quote:
    """A priced booking offer: full price plus the deposit due now."""

    service: Service
    price: Decimal
    deposit: Decimal
    currency: str

    @property
    def balance(self) -> Decimal:
        return self.price - self.deposit

    def describe(self) -> str:
        return (
            f"{self.service.name} — {money(self.price)} {self.currency} "
            f"(deposit {money(self.deposit)} {self.currency} due now, "
            f"balance {money(self.balance)} {self.currency} after the visit)"
        )


@dataclass(frozen=True)
class Business:
    name: str
    agent_name: str
    services: tuple[Service, ...]
    currency: str = "USD"
    deposit_percent: int = 25

    @classmethod
    def from_json(cls, path: str | Path) -> "Business":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict) -> "Business":
        services = tuple(
            Service(
                name=s["name"],
                price_from=Decimal(str(s["price_from"])),
                duration_min=int(s["duration_min"]),
                aliases=tuple(a.lower() for a in s.get("aliases", [])),
                deposit_flat=(
                    Decimal(str(s["deposit_flat"])) if "deposit_flat" in s else None
                ),
            )
            for s in raw["services"]
        )
        if not services:
            raise ValueError("business config must define at least one service")
        deposit_percent = int(raw.get("deposit_percent", 25))
        if not 0 < deposit_percent <= 100:
            raise ValueError("deposit_percent must be in (0, 100]")
        return cls(
            name=raw["name"],
            agent_name=raw.get("agent_name", "Riley"),
            services=services,
            currency=raw.get("currency", "USD"),
            deposit_percent=deposit_percent,
        )

    def service_menu(self) -> str:
        return ", ".join(
            f"{s.name} (from {money(s.price_from)} {self.currency})"
            for s in self.services
        )

    def find_service(self, name_or_alias: str) -> Service | None:
        needle = name_or_alias.strip().lower()
        for svc in self.services:
            if needle == svc.name.lower() or needle in svc.aliases:
                return svc
        # substring match so "Full Interior Detail" matches "interior detail"
        for svc in self.services:
            if needle and (needle in svc.name.lower() or svc.name.lower() in needle):
                return svc
        return None

    def quote(self, service: Service) -> Quote:
        """Price a service and compute the deposit required to book it."""
        price = service.price_from.quantize(_CENTS, rounding=ROUND_HALF_UP)
        if service.deposit_flat is not None:
            deposit = service.deposit_flat.quantize(_CENTS, rounding=ROUND_HALF_UP)
        else:
            deposit = (price * self.deposit_percent / 100).quantize(
                _CENTS, rounding=ROUND_HALF_UP
            )
        if deposit > price:
            raise ValueError("deposit may not exceed the service price")
        return Quote(service=service, price=price, deposit=deposit, currency=self.currency)
