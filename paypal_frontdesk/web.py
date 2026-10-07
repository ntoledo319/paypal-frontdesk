"""Browser chat simulator (stdlib http.server) — the judge-friendly demo.

    python -m paypal_frontdesk.web --mock --port 8080

Serves a single-page chat widget wired to the live agent. The PayPal
approval link the agent prints is a real clickable URL served by the mock
server — click it in the browser, come back, say "paid", and watch the
payment panel move through order → captured → (refund / invoice).
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .agent import FrontDeskAgent
from .config import Business
from .paypal_client import PayPalClient
from .paypal_mock import MockPayPalServer

DEFAULT_BUSINESS_JSON = Path(__file__).resolve().parent.parent / "business.json"

_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>paypal-frontdesk</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: ui-sans-serif, system-ui, sans-serif;
         background: #0b1020; color: #e6e9f2; display: flex; height: 100vh; }
  #chat { flex: 1; display: flex; flex-direction: column; }
  header { padding: 14px 20px; border-bottom: 1px solid #232a45; }
  header h1 { margin: 0; font-size: 16px; }
  header small { color: #8b93b0; }
  #log { flex: 1; overflow-y: auto; padding: 16px 20px; }
  .msg { max-width: 75%; padding: 10px 14px; border-radius: 14px;
         margin: 6px 0; white-space: pre-wrap; line-height: 1.45; font-size: 14px; }
  .agent { background: #1c2440; border-bottom-left-radius: 4px; }
  .you { background: #0d3b66; margin-left: auto; border-bottom-right-radius: 4px; }
  .msg a { color: #6db3ff; word-break: break-all; }
  form { display: flex; gap: 8px; padding: 12px 20px; border-top: 1px solid #232a45; }
  input { flex: 1; padding: 10px 12px; border-radius: 8px; border: 1px solid #2c3554;
          background: #131a30; color: inherit; font-size: 14px; }
  button { padding: 10px 18px; border: 0; border-radius: 8px; background: #2f6df6;
           color: white; font-size: 14px; cursor: pointer; }
  #panel { width: 320px; border-left: 1px solid #232a45; padding: 16px;
           overflow-y: auto; }
  #panel h2 { font-size: 13px; text-transform: uppercase; letter-spacing: .08em;
              color: #8b93b0; }
  .state { font-size: 13px; padding: 6px 10px; border-radius: 6px;
           background: #1c2440; display: inline-block; margin-bottom: 10px; }
  .event { font-size: 12.5px; padding: 8px 10px; border-left: 3px solid #2f6df6;
           background: #131a30; margin: 6px 0; border-radius: 4px; }
  .event.failed { border-color: #e5534b; }
  .event.refunded { border-color: #f0a832; }
  .event.captured, .event.invoice_sent { border-color: #3fb950; }
  .booking { font-size: 12.5px; background: #131a30; padding: 8px 10px;
             border-radius: 6px; margin: 6px 0; }
  .booking b { color: #6db3ff; }
</style>
</head>
<body>
  <div id="chat">
    <header>
      <h1>paypal-frontdesk — AI front desk that collects the money</h1>
      <small id="mode"></small>
    </header>
    <div id="log"></div>
    <form id="f">
      <input id="m" autocomplete="off" placeholder="Type a message… (try: I need a full detail next Friday at 2pm)">
      <button>Send</button>
    </form>
  </div>
  <div id="panel">
    <h2>Conversation state</h2>
    <div class="state" id="state">—</div>
    <h2>Payment trail</h2>
    <div id="trail"></div>
    <h2>Bookings</h2>
    <div id="bookings"></div>
  </div>
<script>
const log = document.getElementById('log');
function linkify(t) {
  return t.replace(/(https?:\\/\\/[^\\s]+)/g, '<a href="$1" target="_blank">$1</a>');
}
function add(role, text) {
  const d = document.createElement('div');
  d.className = 'msg ' + role;
  d.innerHTML = linkify(text.replace(/&/g,'&amp;').replace(/</g,'&lt;'));
  log.appendChild(d); log.scrollTop = log.scrollHeight;
}
function render(s) {
  document.getElementById('state').textContent = s.state;
  document.getElementById('mode').textContent = s.mode;
  document.getElementById('trail').innerHTML = s.trail.map(e =>
    `<div class="event ${e.kind}"><b>${e.kind}</b> — ${e.detail}</div>`).join('') || '<i>none yet</i>';
  document.getElementById('bookings').innerHTML = s.bookings.map(b =>
    `<div class="booking"><b>${b.id}</b> [${b.status}] ${b.service}<br>` +
    `${b.date} ${b.time} — deposit ${b.deposit} ${b.currency}<br>` +
    (b.order_id ? `order ${b.order_id}<br>` : '') +
    (b.capture_id ? `capture ${b.capture_id}<br>` : '') +
    (b.refund_id ? `refund ${b.refund_id}<br>` : '') +
    (b.invoice_id ? `invoice ${b.invoice_id}` : '') +
    `</div>`).join('') || '<i>none yet</i>';
}
fetch('/api/state').then(r => r.json()).then(s => { render(s); add('agent', s.greeting); });
document.getElementById('f').addEventListener('submit', async ev => {
  ev.preventDefault();
  const input = document.getElementById('m');
  const text = input.value.trim(); if (!text) return;
  input.value = ''; add('you', text);
  const r = await fetch('/api/message', {method: 'POST',
    headers: {'Content-Type': 'application/json'}, body: JSON.stringify({message: text})});
  const s = await r.json();
  add('agent', s.reply); render(s);
});
</script>
</body>
</html>
"""


def _state_payload(agent: FrontDeskAgent, mock: MockPayPalServer | None) -> dict:
    return {
        "state": agent.brain.state.name,
        "mode": f"extraction: {agent.extractor_description} · PayPal: "
        + (f"mock @ {mock.base_url}" if mock else "live (env-configured)"),
        "greeting": agent.greeting(),
        "trail": [
            {"kind": e.kind, "detail": e.detail} for e in agent.payment_trail
        ],
        "bookings": [
            {
                "id": b.id,
                "status": b.status,
                "service": b.service,
                "date": b.date,
                "time": b.time,
                "deposit": b.deposit,
                "currency": b.currency,
                "order_id": b.order_id,
                "capture_id": b.capture_id,
                "refund_id": b.refund_id,
                "invoice_id": b.invoice_id,
            }
            for b in agent.bookings.values()
        ],
    }


def make_handler(agent: FrontDeskAgent, mock: MockPayPalServer | None):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: object) -> None:
            pass

        def _json(self, payload: dict, status: int = 200) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/":
                body = _PAGE.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/api/state":
                self._json(_state_payload(agent, mock))
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/api/message":
                self._json({"error": "not found"}, 404)
                return
            length = int(self.headers.get("Content-Length") or 0)
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except json.JSONDecodeError:
                self._json({"error": "bad json"}, 400)
                return
            message = str(payload.get("message", "")).strip()
            reply = agent.handle(message)
            out = _state_payload(agent, mock)
            out["reply"] = reply
            self._json(out)

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Browser chat simulator.")
    parser.add_argument("--mock", action="store_true", help="use the mock PayPal server")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--business", type=Path, default=DEFAULT_BUSINESS_JSON)
    args = parser.parse_args(argv)

    business = Business.from_json(args.business)
    mock = None
    if args.mock:
        mock = MockPayPalServer().start()
        client = PayPalClient(mock.base_url, "mock-id", "mock-secret")
    else:
        client = PayPalClient.from_env()
    agent = FrontDeskAgent(business, client)

    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(agent, mock))
    print(f"Chat simulator: http://127.0.0.1:{args.port}")
    if mock:
        print(f"Mock PayPal:    {mock.base_url}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        if mock:
            mock.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
