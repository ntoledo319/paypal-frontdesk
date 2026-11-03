# Live verification — November 2, 2026

## PayPal sandbox

The repository's `PayPalClient` authenticated against
`https://api-m.sandbox.paypal.com` and completed these operations with simulated
sandbox funds. The order was approved in a browser using the existing PayPal personal
sandbox buyer account. No production API or real payment was used.

| Operation | Evidence | Observed result |
|---|---|---|
| OAuth | Client-credentials grant | Authenticated |
| Read invoices | Invoicing v2 list endpoint | Successful response |
| Create and approve order | `1BF87252MF946253B` | CREATED, then APPROVED |
| Capture USD 1.00 | `6A755702KK595070V` | COMPLETED; USD 1.00 |
| Refund USD 1.00 | `7Y4650070H370844E` | COMPLETED |
| Create and send test invoice | `INV2-LAAX-S7QZ-EPQ6-3P2J` | GET readback SENT |

One order and one invoice were created. The invoice was a separate USD 1.00
sandbox integration check, not a real customer charge or the balance of a
completed appointment. It was sent to the sandbox buyer account, and no invoice
payment was attempted.

The live calls exposed two mismatches hidden by the original mock tests:

- Capture returned HTTP 415 because the bodyless POST omitted JSON media type.
  The client now sends `Content-Type: application/json` and an empty JSON object
  for POST operations without fields. Retrying the same approved order succeeded.
- Invoice creation returned a minimal HATEOAS link (`rel`, `href`, `method`),
  rather than the full resource with `id` returned by the mock. The client now
  normalizes the invoice ID from that resource path. The already-created invoice
  was resumed, sent, and independently fetched; no duplicate was created.

Regression tests cover both response/transport differences. Credentials, access
tokens, buyer passwords, and personal buyer details are not included here.

## Live AI extraction in the application

The actual `FrontDeskAgent` used `make_extractor` and `HybridExtractor` with
OpenRouter model `nvidia/nemotron-3.5-super-vl:free`. The catalog reported zero prompt and completion
prices, and the request also imposed zero prompt, completion and per-request
maximum prices. The successful response reported cost $0.

Synthetic input:

> Please put the reservation under Dana Lee for a full interior detail tomorrow at 2 pm. Contact dana.demo@example.net.

The rules parser did not extract a name. The live LLM supplied `Dana Lee`, and
the application advanced to `CONFIRMING` with the requested service, date, time,
and contact details. This demonstrates a model contribution inside the actual
intake workflow, rather than a provider connectivity test. No PayPal action was
triggered by this intake-only conversation.

## Combined application journey

A subsequent continuous run used the actual `FrontDeskAgent`, `HybridExtractor`,
and `PayPalClient` together. No method output was mocked or substituted. The
existing business configuration quoted USD 120.00 for an interior detail with
a USD 30.00 sandbox deposit and USD 90.00 balance.

1. The customer asked to reserve under Dana Lee. The live free model supplied
   the name that the rules parser missed; the application reached CONFIRMING.
2. Two customer confirmations accepted the details and quote. The orchestrator
   created one sandbox order and supplied its real sandbox approval URL.
3. The order was approved in the browser using the existing sandbox buyer.
   “I just paid” made the orchestrator check approval, capture the deposit, and
   confirm the booking.
4. The customer requested a balance invoice. The orchestrator created and sent
   the USD 90.00 sandbox invoice and returned its identifier.
5. The customer requested cancellation and confirmed it. The orchestrator
   refunded the deposit and closed the conversation.

| Combined-run resource | ID | Independent API readback |
|---|---|---|
| Order | `72018479V3047535B` | COMPLETED |
| Capture | `4AU66916C4938312K` | COMPLETED; USD 30.00 |
| Invoice | `INV2-W2DH-D4LQ-UPQS-H85K` | SENT; USD 90.00 |
| Refund | `77V662198W745232Y` | COMPLETED; USD 30.00 |

This combined run created exactly one additional sandbox order and one invoice.
All LLM calls used the zero-price caps described above; total reported cost was
$0. The final application state was CLOSED. The [sanitized transcript](docs/live-run-20261102.md) and
[machine-readable event log](docs/live-run-20261102.json) retain actual customer
turns, agent replies, payment events, and observed API statuses. Buyer email and
approval URL are replaced with labeled redactions; credentials are never included.

## Scope and reproduction

The full combined conversation is now verified. The older public video and
static replay still show the offline mock lifecycle with rules-based extraction;
they should not be mistaken for footage of this new live sandbox run.

Follow the README for local setup, sandbox environment variables, and the
OpenRouter free-only option. `scripts/sandbox_cycle.py --buyer-email ...` creates
a new sandbox test order and pauses for buyer approval; it does not resume an
existing order. Its default test amount is USD 30.00. All amounts are simulated.

The prototype stores bookings in memory. It does not check a calendar's actual
availability, and cancelling after sending a balance invoice does not cancel
that invoice: the test invoice remains SENT and was not paid. Production
reconciliation and pending transaction handling need additional work; this is a
sandbox prototype result.
