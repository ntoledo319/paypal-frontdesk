# paypal-frontdesk

**An AI front-desk agent that books appointments AND collects the money.**

Most "AI receptionist" demos stop at the calendar invite. This one doesn't:
the agent itself runs the PayPal flow. A customer chats, gets a quote, pays a
deposit through a real PayPal order (create → approve → capture), and the
booking is confirmed only when the money lands. Cancel and the agent issues
the refund. Ask for the bill after the visit and the agent creates and sends
a PayPal invoice for the balance.

Built for the **PayPal AI Hackathon** (Devpost) — target prize: *Best Use of
Agentic Commerce*.

- **Python 3.11+ standard library only.** No pip dependencies, nothing to
  install. (`pytest` is used as the test runner.)
- **Fully offline by default.** A bundled mock PayPal server implements the
  exact REST surface the agent uses, so the whole demo and the whole test
  suite run with zero network access and zero credentials.
- **The same client code runs against the real PayPal sandbox** — only
  environment variables change.
- **Honest AI:** with an `OPENAI_API_KEY` set, slot/intent extraction uses
  an OpenAI-compatible chat-completions endpoint; without one it uses
  deterministic rules-based extraction and says so. Nothing is faked.

## Architecture

```
                customer message
                      │
                      ▼
        ┌──────────────────────────┐
        │   extractor (llm.py)     │   OpenAI-compatible LLM adapter
        │   LLM + rules fallback   │   → deterministic rules if no key
        └────────────┬─────────────┘
                     │ intent + slots (name, service, date, time, contact)
                     ▼
        ┌──────────────────────────┐   states: COLLECTING → CONFIRMING →
        │  dialogue brain          │   QUOTED → AWAITING_DEPOSIT → BOOKED
        │  (brain.py)              │   → (cancel → refund) / (invoice)
        │  pure state machine,     │
        │  emits payment actions   │── create_order / check_payment /
        └────────────┬─────────────┘    refund / create_invoice
                     ▼
        ┌──────────────────────────┐
        │  orchestrator (agent.py) │   books only when money moves;
        │  bookings + payment log  │   every transition recorded
        └────────────┬─────────────┘
                     ▼
        ┌──────────────────────────┐     ┌────────────────────────────┐
        │  PayPal REST client      │────▶│ api-m.sandbox.paypal.com   │
        │  (paypal_client.py)      │  or │          — LIVE            │
        │  stdlib urllib, env creds│     └────────────────────────────┘
        └────────────┬─────────────┘     ┌────────────────────────────┐
                     └───────────────────▶│ mock server (paypal_mock.py)│
                                          │ same endpoints, in-process  │
                                          └────────────────────────────┘
        entry points: cli.py (terminal) · web.py (browser chat widget)
```

PayPal APIs used (v2): `POST /v1/oauth2/token`,
`POST /v2/checkout/orders`, `GET /v2/checkout/orders/{id}`,
`POST /v2/checkout/orders/{id}/capture`,
`POST /v2/payments/captures/{id}/refund`,
`POST /v2/invoicing/invoices`, `POST /v2/invoicing/invoices/{id}/send`.

## Quickstart (mock mode — offline, no credentials)

```bash
git clone https://github.com/ntoledo319/paypal-frontdesk.git
cd paypal-frontdesk

# scripted end-to-end demo: book + pay deposit, send invoice, cancel + refund
python3 -m paypal_frontdesk.cli --demo

# interactive chat in your terminal (type 'approve' to open the PayPal link)
python3 -m paypal_frontdesk.cli --mock

# browser chat widget with a live payment-state panel
python3 -m paypal_frontdesk.web --mock --port 8080
# → open http://127.0.0.1:8080
```

The browser widget renders the bookings table with **AG Grid Community**
(sortable/filterable booking, payment, and invoice columns), vendored at
`paypal_frontdesk/static/ag-grid-community.min.js` (MIT — see
`THIRD-PARTY-LICENSES.md`) and served locally so the demo works fully offline.

The scripted demo prints the full money trail, e.g.:

```
=== PAYMENT TRAIL ===
  BK-0001 [REFUNDED] order=ORDER-000001 capture=CAPTURE-000002 refund=REFUND-000004 invoice=INV-000003
```

In mock mode the PayPal approval link the agent prints is a real URL served
by the mock server — click it (or type `approve`) to simulate the customer
approving the payment, exactly as they would on paypal.com.

## Sandbox mode (real PayPal)

1. Create a sandbox app at <https://developer.paypal.com> → *Apps &
   Credentials* → copy the **client id** and **secret**.
2. Export three environment variables:

   ```bash
   export PAYPAL_BASE_URL="https://api-m.sandbox.paypal.com"
   export PAYPAL_CLIENT_ID="your-client-id"
   export PAYPAL_CLIENT_SECRET="your-client-secret"
   ```
3. Run without `--mock`:

   ```bash
   python3 -m paypal_frontdesk.cli
   ```

The approval link now points at paypal.com — open it in a browser logged
into a sandbox **buyer** account, approve, return to the chat, say "paid".
Credentials are read from the environment at runtime only; they are never
stored, logged, or printed.

## LLM extraction (optional)

The agent needs to pull intent + slots (name, service, date, time,
phone/email) out of free-text messages. Two honest modes:

| Configuration | Behavior |
|---|---|
| nothing set | **Rules-based extraction** (deterministic regex/date parsing). The CLI and web UI state this plainly. |
| `OPENAI_API_KEY` set (+ optional `OPENAI_BASE_URL`, `OPENAI_MODEL`) | OpenAI-compatible **chat-completions** extraction, with rules as a fallback and tie-breaker. Any LLM failure falls back to rules. |

```bash
export OPENAI_BASE_URL="https://api.openai.com"   # or any compatible endpoint
export OPENAI_API_KEY="sk-..."
export OPENAI_MODEL="gpt-4o-mini"
```

## Testing (for judges)

```bash
cd paypal-frontdesk
python3 -m pytest        # 68 tests, ~25s, fully offline
```

The suite covers: dialogue-brain transitions, slot extraction (rules path
and LLM adapter with a stubbed transport), mock-server round-trips
(oauth, orders, capture, refund, invoices), the full agent flow
(book+pay, cancel+refund, invoice), error paths (capture failure, expired
order → fresh link, unapproved capture, double refund, 404s), and
environment-config behavior. Mock servers run on ephemeral ports; nothing
leaves localhost.

## Configuration

`business.json` describes the business: services, prices, aliases, and the
deposit policy (`deposit_percent` business-wide, or `deposit_flat` per
service). Point the CLI at your own with `--business path/to/yours.json`.

## Disclosure

Disclosure: this project was built and is submitted by an AI agent (Ledger) on behalf of Toledo Technologies LLC; Nick Toledo is the LLC's authorized representative. The LLM extraction adapter is optional; without an API key the agent uses deterministic rules-based extraction.

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 Nicholas Toledo.
