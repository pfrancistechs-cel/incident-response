#!/usr/bin/env python3
"""The shop that gets paged about (stdlib only). Faults are injected at runtime:
  mode ok    healthy
  mode pool  the database connection pool is exhausted: every checkout fails with a 500   (fix: POST /_admin/reset-pool)
  mode slow  every checkout takes SLOW_SECONDS longer                                     (no automatic fix: a human must act)
  mode both  pool + slow
Metrics for Prometheus at /metrics."""
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", "8080"))
SLOW_SECONDS = float(os.environ.get("SLOW_SECONDS", "0.8"))
LOCK = threading.Lock()
STATE = {"mode": "ok", "requests": {}, "seconds_sum": 0.0, "seconds_count": 0, "changed": time.time()}
MODES = ("ok", "pool", "slow", "both")


def set_mode(mode):
    with LOCK:
        STATE["mode"] = mode
        STATE["changed"] = time.time()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def reply(self, code, body, ctype="application/json"):
        raw = body if isinstance(body, bytes) else (body if isinstance(body, str) else json.dumps(body)).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path == "/healthz":
            return self.reply(200, {"ok": True})
        if path == "/checkout":
            started = time.time()
            mode = STATE["mode"]
            if mode in ("slow", "both"):
                time.sleep(SLOW_SECONDS)
            code, body = (500, {"error": "connection pool exhausted"}) if mode in ("pool", "both") else (200, {"checkout": "ok"})
            with LOCK:
                STATE["requests"][code] = STATE["requests"].get(code, 0) + 1
                STATE["seconds_sum"] += time.time() - started
                STATE["seconds_count"] += 1
            return self.reply(code, body)
        if path == "/_stats":
            with LOCK:
                return self.reply(200, {"mode": STATE["mode"], "requests": {str(k): v for k, v in STATE["requests"].items()},
                                        "changed": STATE["changed"]})
        if path == "/metrics":
            with LOCK:
                lines = ["# TYPE ir_requests_total counter"]
                lines += [f'ir_requests_total{{code="{c}"}} {n}' for c, n in sorted(STATE["requests"].items())]
                lines += ["# TYPE ir_request_seconds summary", f"ir_request_seconds_sum {STATE['seconds_sum']:.6f}",
                          f"ir_request_seconds_count {STATE['seconds_count']}"]
                lines += [f'ir_fault_mode{{mode="{m}"}} {1 if STATE["mode"] == m else 0}' for m in MODES]
            return self.reply(200, "\n".join(lines) + "\n", "text/plain; version=0.0.4")
        self.reply(404, {"error": "not found"})

    def do_POST(self):
        path, _, query = self.path.partition("?")
        params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
        if path == "/_fault":
            if params.get("mode") not in MODES:
                return self.reply(400, {"error": "mode must be one of " + ", ".join(MODES)})
            set_mode(params["mode"])
            return self.reply(200, {"mode": params["mode"]})
        if path == "/_admin/reset-pool":              # the runbook's fix: it clears pool exhaustion, not slowness
            with LOCK:
                before = STATE["mode"]
            after = {"pool": "ok", "both": "slow"}.get(before, before)
            set_mode(after)
            return self.reply(200, {"before": before, "after": after})
        self.reply(404, {"error": "not found"})


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    srv.daemon_threads = True
    print(f"app on :{PORT}", flush=True)
    srv.serve_forever()
