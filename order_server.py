"""CLOB order server — lightweight HTTP wrapper around CLOB SDK.
   Go calls GET /buy?token=...&amount=... or GET /sell?token=...&shares=...
"""
import json
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.clob_types import MarketOrderArgsV2
from config import PRIVATE_KEY, PROXY, CLOB_URL, CHAIN_ID

logging.basicConfig(level=logging.INFO, format="%(asctime)s [order] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("order_server")

_client = None


def get_client():
    global _client
    if _client is None:
        temp = ClobClient(CLOB_URL, key=PRIVATE_KEY, chain_id=CHAIN_ID)
        creds = temp.create_or_derive_api_key()
        _client = ClobClient(CLOB_URL, key=PRIVATE_KEY, chain_id=CHAIN_ID,
                              creds=creds, signature_type=1, funder=PROXY)
        log.info("CLOB client ready")
    return _client


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if parsed.path == "/buy":
            token_id = params.get("token", [""])[0]
            amount = float(params.get("amount", ["0"])[0])
            if not token_id or amount <= 0:
                self.send_error(400, "missing token or amount")
                return
            log.info(f"BUY token={token_id[:20]}... amount=${amount:.2f}")
            client = get_client()
            try:
                args = MarketOrderArgsV2(token_id=token_id, amount=amount, side="BUY")
                resp = client.create_and_post_market_order(args, order_type="FOK")
                self.send_json(resp)
            except Exception as e:
                self.send_json({"error": str(e), "success": False})

        elif parsed.path == "/sell":
            token_id = params.get("token", [""])[0]
            shares = float(params.get("shares", ["0"])[0])
            if not token_id or shares <= 0:
                self.send_error(400, "missing token or shares")
                return
            log.info(f"SELL token={token_id[:20]}... shares={shares:.1f}")
            client = get_client()
            try:
                args = MarketOrderArgsV2(token_id=token_id, amount=shares, side="SELL")
                resp = client.create_and_post_market_order(args, order_type="FOK")
                self.send_json(resp)
            except Exception as e:
                self.send_json({"error": str(e), "success": False})

        elif parsed.path == "/health":
            self.send_json({"ok": True})
        else:
            self.send_error(404)

    def send_json(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # suppress default HTTP logging


def main():
    port = 8765
    log.info(f"Order server on :{port}")
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
