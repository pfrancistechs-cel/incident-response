#!/usr/bin/env python3
"""oncall: a tiny stand-in for PagerDuty / Opsgenie (stdlib only).

  * receives Alertmanager's webhook (POST /alert) and opens an incident per firing alert
  * pages the primary on-call; if nobody acknowledges within ACK_TIMEOUT seconds it escalates to the secondary
  * runs the alert's runbook automatically when there is one (label runbook=reset-pool): checks, fixes, verifies
  * closes the incident when Alertmanager says the alert resolved, and writes a postmortem draft from the timeline
  * serves the console and the JSON API on CONSOLE_PORT (and the same API on API_PORT, where Alertmanager posts)
Paging here only writes to the pager feed. A real service would send an SMS or a phone call."""
import json
import os
import threading
import time
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE_DIR = os.environ.get("STATE_DIR", "/state")
API_PORT = int(os.environ.get("API_PORT", "9094"))
CONSOLE_PORT = int(os.environ.get("CONSOLE_PORT", "8081"))
APP_URL = os.environ.get("APP_URL", "http://app:8080")
AM_URL = os.environ.get("AM_URL", "http://alertmanager:9093")
ACK_TIMEOUT = float(os.environ.get("ACK_TIMEOUT", "15"))
INCIDENTS_FILE = os.path.join(STATE_DIR, "incidents.json")
SCHEDULE_FILE = os.path.join(STATE_DIR, "oncall.json")
PM_DIR = os.path.join(STATE_DIR, "postmortems")
LOCK = threading.RLock()
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def now():
    return round(time.time(), 2)


def load(path, default):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def save(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)


INCIDENTS = load(INCIDENTS_FILE, [])
SCHEDULE = load(SCHEDULE_FILE, {"primary": "alice", "secondary": "bob"})
PAGES = []                                   # the pager feed: who was paged, when, for what
SETTINGS = {"auto_remediation": os.environ.get("AUTO_REMEDIATE", "true").lower() != "false"}


def persist():
    save(INCIDENTS_FILE, INCIDENTS)


def event(inc, kind, detail):
    inc["timeline"].append({"ts": now(), "event": kind, "detail": detail})


def page(inc, who, role):
    PAGES.append({"ts": now(), "who": who, "role": role, "incident": inc["id"], "alert": inc["alert"]})
    del PAGES[:-50]
    event(inc, "page", f"paged {who} ({role})")


def parse_time(text):
    try:
        text = text.rstrip("Z")
        head, _, frac = text.partition(".")
        return datetime.fromisoformat(head + ("." + frac[:6] if frac else "") + "+00:00").timestamp()
    except Exception:
        return now()


def find_open(alert):
    return next((i for i in INCIDENTS if i["alert"] == alert and i["status"] != "resolved"), None)


def open_incident(alert):
    labels, ann = alert["labels"], alert.get("annotations", {})
    n = max([i["n"] for i in INCIDENTS], default=0) + 1
    inc = {"id": f"INC-{n:04d}", "n": n, "alert": labels.get("alertname", "unknown"), "severity": labels.get("severity", ""),
           "service": labels.get("service", ""), "summary": ann.get("summary", ""), "runbook": labels.get("runbook"),
           "status": "triggered", "started": parse_time(alert.get("startsAt", "")), "created": now(), "assignee": SCHEDULE["primary"],
           "acked_by": None, "acked_at": None, "escalated_at": None, "resolved_at": None, "mitigated_by": None,
           "ack_deadline": now() + ACK_TIMEOUT, "timeline": []}
    event(inc, "alert", f"{inc['alert']} fired: {inc['summary']}")
    page(inc, SCHEDULE["primary"], "primary")
    INCIDENTS.append(inc)
    persist()
    if inc["runbook"] == "reset-pool" and SETTINGS["auto_remediation"]:
        threading.Thread(target=run_runbook, args=(inc["id"],), daemon=True).start()
    return inc


def http(method, url, timeout=5):
    req = urllib.request.Request(url, method=method, data=b"" if method == "POST" else None)
    with OPENER.open(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode()


def run_runbook(inc_id):
    """Runbook reset-pool: look, fix, verify. Every step goes into the incident timeline."""
    def log(kind, detail):
        with LOCK:
            inc = next(i for i in INCIDENTS if i["id"] == inc_id)
            event(inc, kind, detail)
            persist()
    try:
        log("runbook", "reset-pool started (automatic)")
        _, body = http("GET", APP_URL + "/_stats")
        log("runbook", f"step 1: checked the app, fault mode is {json.loads(body)['mode']}")
        _, body = http("POST", APP_URL + "/_admin/reset-pool")
        log("runbook", f"step 2: reset the connection pool ({json.loads(body)['before']} -> {json.loads(body)['after']})")
        time.sleep(2)
        codes = []
        for _ in range(5):
            try:
                codes.append(http("GET", APP_URL + "/checkout?user=runbook", timeout=8)[0])
            except Exception:
                codes.append(0)
        good = codes.count(200)
        log("runbook", f"step 3: verified, {good} of 5 checkouts succeeded")
        with LOCK:
            inc = next(i for i in INCIDENTS if i["id"] == inc_id)
            if good == 5:
                inc["mitigated_by"] = "runbook reset-pool"
            persist()
        log("runbook", "reset-pool finished: " + ("OK" if good == 5 else "FAILED, a human must take over"))
    except Exception as exc:
        log("runbook", f"reset-pool FAILED: {str(exc)[:80]}")


def escalation_loop():
    while True:
        time.sleep(0.5)
        with LOCK:
            for inc in INCIDENTS:
                if inc["status"] == "triggered" and now() > inc["ack_deadline"]:
                    inc["status"] = "escalated"
                    inc["escalated_at"] = now()
                    inc["assignee"] = SCHEDULE["secondary"]
                    event(inc, "escalation", f"nobody acknowledged within {ACK_TIMEOUT:g}s: escalating")
                    page(inc, SCHEDULE["secondary"], "secondary")
                    persist()


def ack(inc_id, who):
    with LOCK:
        inc = next((i for i in INCIDENTS if i["id"] == inc_id), None)
        if inc is None:
            return 404, {"error": "no such incident"}
        if inc["status"] == "resolved":
            return 409, {"error": "already resolved"}
        if inc["acked_by"]:
            return 200, inc
        inc["acked_by"], inc["acked_at"] = who, now()
        inc["status"] = "acknowledged"
        event(inc, "ack", f"acknowledged by {who}")
        persist()
        return 200, inc


def resolve(inc, alert):
    inc["status"] = "resolved"
    inc["resolved_at"] = now()
    event(inc, "resolved", "Alertmanager reports the alert resolved")
    persist()
    os.makedirs(PM_DIR, exist_ok=True)
    with open(os.path.join(PM_DIR, inc["id"] + ".md"), "w") as fh:
        fh.write(postmortem(inc))


def handle_webhook(payload):
    with LOCK:
        for alert in payload.get("alerts", []):
            name = alert["labels"].get("alertname")
            inc = find_open(name)
            if alert["status"] == "firing" and inc is None:
                open_incident(alert)
            elif alert["status"] == "resolved" and inc is not None:
                resolve(inc, alert)


def fmt_time(ts):
    return time.strftime("%H:%M:%S", time.localtime(ts))


def span(seconds):
    return "n/a" if seconds is None else f"{seconds:.0f}s"


def postmortem(inc):
    created, acked, resolved = inc["created"], inc["acked_at"], inc["resolved_at"]
    tta = acked - created if acked else None
    ttr = resolved - created if resolved else None
    escalated = inc["escalated_at"] is not None
    mitigation = inc["mitigated_by"] or "manual (no automatic fix worked or exists)"
    went_well, went_wrong, actions = [], [], []
    if inc["started"] and created - inc["started"] < 60:
        went_well.append(f"The alert reached a human {created - inc['started']:.0f}s after it started firing.")
    if inc["mitigated_by"]:
        went_well.append(f"The runbook fixed it automatically ({inc['mitigated_by']}).")
        actions.append("Find and fix the root cause: the automatic fix hides it, and it will come back.")
    if tta is not None and not escalated:
        went_well.append(f"The primary on-call acknowledged within {tta:.0f}s.")
    if escalated:
        went_wrong.append(f"Nobody acknowledged within {ACK_TIMEOUT:g}s, so the page was escalated to the secondary.")
        actions.append("Check why the primary did not acknowledge (phone settings, schedule, ack timeout).")
    if not inc["runbook"]:
        went_wrong.append(f"{inc['alert']} has no runbook: the responder started from nothing.")
        actions.append(f"Write a runbook for {inc['alert']} and link it in the alert.")
    if acked is None:
        went_wrong.append("Nobody acknowledged the incident before it resolved.")
    if not went_well:
        went_well.append("TODO")
    if not went_wrong:
        went_wrong.append("TODO")
    actions.append("Review the alert threshold: was it too early, too late or just right?")
    rows = "\n".join(f"| {fmt_time(e['ts'])} | {e['event']}: {e['detail']} |" for e in inc["timeline"])
    return f"""# Postmortem: {inc['id']} {inc['alert']}

Status: DRAFT, generated from the incident timeline. Edit the sections marked TODO.

## Summary
- Alert: {inc['alert']} (severity {inc['severity']}, service {inc['service']})
- What it means: {inc['summary']}
- Alert active from {fmt_time(inc['started'])} to {fmt_time(resolved) if resolved else 'still open'}

## Impact
TODO: who was affected and for how long. Prometheus and the console hold the request numbers.

## Metrics
| Measure | Value |
|---|---|
| Time to page (alert active to page) | {span(created - inc['started'])} |
| Time to acknowledge | {span(tta)} |
| Time to resolve | {span(ttr)} |
| Escalated | {'yes, after ' + format(inc['escalated_at'] - created, '.0f') + 's' if escalated else 'no'} |
| Mitigation | {mitigation} |

## Timeline
| Time | Event |
|---|---|
{rows}

## Root cause
TODO

## What went well
{chr(10).join('- ' + x for x in went_well)}

## What went wrong
{chr(10).join('- ' + x for x in went_wrong)}

## Action items
{chr(10).join('- [ ] ' + x for x in actions)}
"""


def alertmanager_alerts():
    try:
        _, body = http("GET", AM_URL + "/api/v2/alerts", timeout=2)
        return [{"alert": a["labels"].get("alertname"), "state": a["status"]["state"], "silenced": bool(a["status"]["silencedBy"]),
                 "inhibited": bool(a["status"]["inhibitedBy"])} for a in json.loads(body)]
    except Exception as exc:
        return {"error": str(exc)[:80]}


def snapshot():
    am = alertmanager_alerts()                  # a network call: not under the lock
    with LOCK:
        return {"schedule": dict(SCHEDULE), "settings": dict(SETTINGS), "ack_timeout": ACK_TIMEOUT,
                "incidents": json.loads(json.dumps(INCIDENTS[-15:])), "pages": list(PAGES[-12:]), "alertmanager": am}


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>On-call</title><style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#16202a;--mut:#5d6b78;--line:#dfe4ea;--ok:#1a7f4b;--bad:#c62828;--warn:#b26a00;--blue:#2563eb}
@media(prefers-color-scheme:dark){:root{--bg:#0f1419;--card:#18202a;--ink:#e6edf3;--mut:#8b98a5;--line:#2a3441;--ok:#3fb27f;--bad:#ef6b6b;--warn:#e0a03a;--blue:#5b8def}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,sans-serif}
main{max-width:1060px;margin:0 auto;padding:16px}h1{font-size:20px;margin:4px 0 14px}h2{font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--mut);margin:0 0 10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;margin-bottom:14px;overflow-x:auto}.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
table{width:100%;border-collapse:collapse;font-size:13px}td,th{text-align:left;padding:6px;border-top:1px solid var(--line);white-space:nowrap}th{color:var(--mut);font-weight:500}
.pill{font-size:12px;padding:2px 9px;border-radius:99px;border:1px solid var(--line)}.triggered,.escalated{color:var(--bad);border-color:var(--bad)}.acknowledged{color:var(--warn);border-color:var(--warn)}.resolved{color:var(--ok);border-color:var(--ok)}
button{font:inherit;font-size:12px;padding:3px 9px;border-radius:7px;border:1px solid var(--line);background:var(--card);color:var(--ink);cursor:pointer}a{color:var(--blue)}
.mut{color:var(--mut);font-size:13px}.tl td:first-child{color:var(--mut);width:70px}@media(max-width:700px){.grid{grid-template-columns:1fr}}
</style></head><body><main><h1>On-call <span class="mut" id="meta"></span></h1>
<div class="card"><h2>Incidents</h2><table><thead><tr><th>ID</th><th>Alert</th><th>Status</th><th>Assignee</th><th>Ack</th><th>Resolve</th><th></th></tr></thead><tbody id="inc"></tbody></table></div>
<div class="grid"><div class="card"><h2 id="tlh">Timeline</h2><table class="tl"><tbody id="tl"></tbody></table></div>
<div><div class="card"><h2>Pager feed</h2><table><tbody id="pg"></tbody></table></div>
<div class="card"><h2>Alertmanager</h2><table><tbody id="am"></tbody></table></div></div></div>
</main><script>
const $=id=>document.getElementById(id),t=s=>new Date(s*1000).toLocaleTimeString([], {hour12:false});
async function ack(id){await fetch('/api/incidents/'+id+'/ack',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({who:$('meta').dataset.primary})});tick()}
function render(s){
 $('meta').dataset.primary=s.schedule.primary;$('meta').textContent='primary '+s.schedule.primary+', secondary '+s.schedule.secondary+', ack timeout '+s.ack_timeout+'s, auto-remediation '+(s.settings.auto_remediation?'on':'off');
 const list=s.incidents.slice().reverse();
 $('inc').innerHTML=list.map(i=>`<tr><td><b>${i.id}</b></td><td>${i.alert}</td><td><span class="pill ${i.status}">${i.status}</span></td><td>${i.assignee}</td>
  <td>${i.acked_at?Math.round(i.acked_at-i.created)+'s by '+i.acked_by:'-'}</td><td>${i.resolved_at?Math.round(i.resolved_at-i.created)+'s':'-'}</td>
  <td>${i.status==='resolved'?`<a href="/api/incidents/${i.id}/postmortem" target="_blank">postmortem</a>`:(i.acked_by?'':`<button onclick="ack('${i.id}')">Acknowledge</button>`)}</td></tr>`).join('')||'<tr><td colspan=7 class="mut">no incidents</td></tr>';
 const cur=list[0];$('tlh').textContent=cur?'Timeline '+cur.id:'Timeline';
 $('tl').innerHTML=cur?cur.timeline.map(e=>`<tr><td>${t(e.ts)}</td><td><b>${e.event}</b> ${e.detail}</td></tr>`).join(''):'';
 $('pg').innerHTML=s.pages.slice().reverse().map(p=>`<tr><td>${t(p.ts)}</td><td>paged <b>${p.who}</b> (${p.role})</td><td>${p.incident}</td></tr>`).join('')||'<tr><td class="mut">nobody paged yet</td></tr>';
 $('am').innerHTML=Array.isArray(s.alertmanager)?(s.alertmanager.map(a=>`<tr><td>${a.alert}</td><td>${a.silenced?'silenced':a.inhibited?'inhibited':a.state}</td></tr>`).join('')||'<tr><td class="mut">no alerts</td></tr>'):`<tr><td class="mut">unreachable: ${s.alertmanager.error}</td></tr>`}
async function tick(){try{render(await (await fetch('/api/state')).json())}catch(e){}}
tick();setInterval(tick,1000);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json"):
        raw = body if isinstance(body, bytes) else (body if isinstance(body, str) else json.dumps(body)).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(n) or b"{}")
            return data if isinstance(data, dict) else {}
        except ValueError:
            return {}

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/") or "/"
        if path == "/healthz":
            return self.send(200, {"ok": True})
        if path == "/api/state":
            return self.send(200, snapshot())
        if path == "/api/incidents":
            with LOCK:
                return self.send(200, INCIDENTS)
        if path == "/api/settings":
            return self.send(200, SETTINGS)
        parts = path.strip("/").split("/")
        if parts[:2] == ["api", "incidents"] and len(parts) >= 3:
            with LOCK:
                inc = next((i for i in INCIDENTS if i["id"] == parts[2]), None)
                if inc is None:
                    return self.send(404, {"error": "no such incident"})
                if parts[3:] == ["postmortem"]:
                    return self.send(200, postmortem(inc), "text/markdown; charset=utf-8")
                return self.send(200, inc)
        if path == "/":
            return self.send(200, PAGE.encode(), "text/html; charset=utf-8")
        self.send(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?")[0].rstrip("/")
        body = self.body()
        if path in ("/alert", "/api/alert"):
            handle_webhook(body)
            return self.send(200, {"ok": True})
        if path == "/api/settings":
            if isinstance(body.get("auto_remediation"), bool):
                SETTINGS["auto_remediation"] = body["auto_remediation"]
            return self.send(200, SETTINGS)
        if path == "/api/schedule":
            with LOCK:
                for role in ("primary", "secondary"):
                    if isinstance(body.get(role), str) and body[role]:
                        SCHEDULE[role] = body[role]
                save(SCHEDULE_FILE, SCHEDULE)
            return self.send(200, SCHEDULE)
        parts = path.strip("/").split("/")
        if parts[:2] == ["api", "incidents"] and len(parts) == 4 and parts[3] == "ack":
            code, data = ack(parts[2], str(body.get("who") or "someone")[:40])
            return self.send(code, data)
        self.send(404, {"error": "not found"})


def serve(port):
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    srv.daemon_threads = True
    srv.serve_forever()


if __name__ == "__main__":
    os.makedirs(STATE_DIR, exist_ok=True)
    threading.Thread(target=escalation_loop, daemon=True).start()
    threading.Thread(target=serve, args=(CONSOLE_PORT,), daemon=True).start()
    print(f"oncall: webhook on :{API_PORT}, console on :{CONSOLE_PORT}", flush=True)
    serve(API_PORT)
