"""paypal-frontdesk — an AI front-desk agent that books appointments AND
collects the money.

Conversation → quote → PayPal order (create → approve → capture) → booking
confirmed. Cancellation → refund. Post-visit balance → PayPal invoice.

Pure Python 3.11+ standard library. No pip dependencies.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
