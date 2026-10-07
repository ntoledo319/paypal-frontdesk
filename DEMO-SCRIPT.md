# Demo video script — paypal-frontdesk (≈2 minutes)

## 0:00–0:20 — The problem

> "Small service businesses lose real money two ways: missed calls, and
> no-shows. An AI receptionist that just books a calendar slot fixes the
> first one — but a booking with no money behind it is still a maybe.
> paypal-frontdesk is an AI front-desk agent that books the appointment
> AND collects the deposit, in the same conversation. The agent itself runs
> the PayPal flow."

## 0:20–0:55 — Live booking with payment (CLI, mock PayPal)

Run: `python3 -m paypal_frontdesk.cli --demo`

> "A customer messages the shop. The agent extracts the name, service, date,
> time, and contact details — and here's the important part — it quotes a
> price and requires a 25% deposit to lock the booking.
>
> The agent creates a real PayPal order — v2 checkout/orders — and hands the
> customer a secure approval link. The customer approves, says 'paid', and
> the agent checks the order status, captures the payment, and only THEN
> confirms the booking. There's the capture id. Money moved before the
> calendar did."

(Highlight on screen: `ORDER-000001`, the approval link, `CAPTURE-000002`.)

## 0:55–1:20 — Invoice + cancellation refund

> "After the visit, the customer asks for the bill — the agent creates and
> sends a PayPal invoice for the remaining balance, straight to their email.
>
> And if plans change: the customer cancels, the agent confirms, and refunds
> the deposit in full through the payments API. Refund id, right there.
> Order, capture, invoice, refund — every cent accounted for in the payment
> trail."

(Highlight: `INV-…`, `REFUND-…`, and the final `=== PAYMENT TRAIL ===` block.)

## 1:20–1:40 — It's real, and it's tested

Run: `python3 -m pytest -q`

> "Everything you just saw runs on the actual PayPal v2 REST surface —
> checkout orders, captures, refunds, invoicing. The bundled mock server
> implements the same endpoints, so the demo runs offline, but point three
> environment variables at api-m.sandbox.paypal.com and the identical code
> runs against the real sandbox. 68 tests, all passing: dialogue
> transitions, slot extraction, the full book-and-pay flow, refunds,
> invoices, and the failure paths — capture failures, expired orders,
> unapproved captures."

## 1:40–2:00 — Close

> "This is agentic commerce in the literal sense: the AI agent doesn't just
> talk about the appointment — it creates the order, verifies the approval,
> captures the deposit, sends the invoice, and issues the refund. Pure
> Python standard library, no dependencies, MIT licensed.
> paypal-frontdesk — the front desk that collects the money."

---

### Production notes

- Record the terminal at a large font; run `--demo` once before recording to
  confirm output, then record the real run (ids increment per run — that's
  fine, they regenerate).
- Optional cutaway: `python3 -m paypal_frontdesk.web --mock --port 8080`
  shows the same flow as a browser chat widget with a live payment-state
  panel — good B-roll for the 0:20–0:55 section.
- Keep the final payment-trail block on screen for ~2 seconds; judges look
  for the four ids.
