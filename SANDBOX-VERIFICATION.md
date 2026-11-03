# Live verification — November 2, 2026

## PayPal sandbox

The repository's `PayPalClient` authenticated against
`https://api-m.sandbox.paypal.com` and completed these operations with simulated
sandbox funds. The owner approved the order using a PayPal personal sandbox
buyer account. No production API or real payment was used.

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

## Scope and reproduction

These are **two separately observed checks**: the live PayPal client lifecycle
and live LLM-driven application intake. They are not evidence of a single recorded
end-to-end AI + sandbox conversation. The existing public video and static replay
show the offline mock lifecycle with rules-based extraction.

Follow the README for local setup, sandbox environment variables, and the
OpenRouter free-only option. `scripts/sandbox_cycle.py --buyer-email ...` creates
a new sandbox test order and pauses for buyer approval; it does not resume an
existing order. Its default test amount is USD 30.00. All amounts are simulated.

The prototype stores bookings in memory. It does not check a calendar's actual
availability, and cancelling after sending a balance invoice does not cancel
that invoice. Production reconciliation and pending transaction handling need
additional work; this verification is a sandbox prototype result.
