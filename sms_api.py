#!/usr/bin/env python3
import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

from sms_cli import load_history, prune_history, parse_ts


def _filter_since(records, since_value):
    if not since_value:
        return records
    since = parse_ts(since_value)
    if not since:
        return records
    return [r for r in records if parse_ts(r.get("ts")) and parse_ts(r.get("ts")) >= since]


def _apply_limit(records, limit_value):
    if not limit_value:
        return records
    try:
        limit = int(limit_value)
    except Exception:
        return records
    if limit <= 0:
        return records
    return records[-limit:]


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._send_json(200, {"status": "ok"})
            return
        if parsed.path != "/history":
            self._send_json(404, {"error": "not_found"})
            return

        params = parse_qs(parsed.query)
        since = params.get("since", [""])[0]
        limit = params.get("limit", [""])[0]

        records = prune_history(load_history())
        records = _filter_since(records, since)
        records = _apply_limit(records, limit)

        self._send_json(200, {"count": len(records), "items": records})

    def log_message(self, format, *args):
        return


def main():
    parser = argparse.ArgumentParser(description="SMS history API")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind")
    parser.add_argument("--port", type=int, default=8081, help="Port to bind")
    args = parser.parse_args()

    httpd = HTTPServer((args.host, args.port), Handler)
    print(f"SMS history API listening on http://{args.host}:{args.port}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
