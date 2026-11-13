#!/usr/bin/env python3
"""Incident-response command line. Usage:
  ir.py status                      incidents, schedule, Alertmanager alerts
  ir.py fault ok|pool|slow|both     inject a fault into the shop
  ir.py automation on|off           automatic runbooks on or off
  ir.py ack ID|latest WHO           acknowledge an incident
  ir.py postmortem ID|latest        print the generated postmortem
  ir.py silence ALERT MINUTES       Alertmanager silence (a maintenance window); ir.py unsilence removes the ones made here
  ir.py last-id                     highest incident number so far (0 if none)
  ir.py summary SINCE ALERT         one line: how fast it was acknowledged and resolved
  ir.py check WHAT ...              exit code 0 = true (used by demo.sh and smoke.sh), see the code"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

APP = os.environ.get("APP_URL", "http://127.0.0.1:8080")
ONCALL = os.environ.get("ONCALL_URL", "http://127.0.0.1:8081")
AM = os.environ.get("AM_URL", "http://127.0.0.1:9093")
PROM = os.environ.get("PROM_URL", "http://127.0.0.1:9090")
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(method, url, body=None, timeout=6):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else (b"" if method == "POST" else None),
                                 headers={"Content-Type": "application/json"})
    try:
        with OPENER.open(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def jget(url):
    return json.loads(call("GET", url)[1])


def incidents():
    return jget(ONCALL + "/api/incidents")


def since_list(since, alert):
    return [i for i in incidents() if i["n"] > int(since) and (alert is None or i["alert"] == alert)]


def am_alerts():
    return jget(AM + "/api/v2/alerts")


def iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def check(what, a):
    if what == "ready":
        stats = jget(APP + "/_stats")
        first = sum(stats["requests"].values())
        time.sleep(1.2)
        return (call("GET", PROM + "/-/ready")[0] == 200 and call("GET", AM + "/-/ready")[0] == 200
                and call("GET", ONCALL + "/healthz")[0] == 200 and sum(jget(APP + "/_stats")["requests"].values()) > first)
    if what == "prom-rules":
        text = call("GET", PROM + "/api/v1/rules")[1]
        return "HighErrorRate" in text and "HighLatency" in text
    if what == "prom-target":
        return '"health":"up"' in call("GET", PROM + "/api/v1/targets")[1]
    if what == "prom-am":
        return bool(jget(PROM + "/api/v1/alertmanagers")["data"]["activeAlertmanagers"])
    if what == "oncall-sees-am":
        return isinstance(jget(ONCALL + "/api/state")["alertmanager"], list)
    if what == "page-served":
        return "On-call" in call("GET", ONCALL + "/")[1]
    if what == "app-mode":
        return jget(APP + "/_stats")["mode"] == a[0]
    if what == "app-ok":
        return jget(APP + "/_stats")["mode"] == "ok" and call("GET", APP + "/checkout?user=check")[0] == 200
    if what == "app-failing":
        return call("GET", APP + "/checkout?user=check")[0] == 500
    if what == "quiet":
        return not am_alerts() and all(i["status"] == "resolved" for i in incidents()) and jget(APP + "/_stats")["mode"] == "ok"
    if what == "incident":                       # SINCE ALERT STATUS...
        return any(i["status"] in a[2:] for i in since_list(a[0], a[1]))
    if what == "no-incident":                    # SINCE ALERT
        return not since_list(a[0], a[1])
    if what == "incident-count":                 # SINCE N
        return len(since_list(a[0], None)) == int(a[1])
    if what == "paged":                          # SINCE ALERT WHO
        return any(e["event"] == "page" and f"paged {a[2]} " in e["detail"] for i in since_list(a[0], a[1]) for e in i["timeline"])
    if what == "acked":                          # SINCE ALERT WHO
        return any(i["acked_by"] == a[2] for i in since_list(a[0], a[1]))
    if what == "runbook-ok":                     # SINCE ALERT
        return any(i["mitigated_by"] == "runbook reset-pool" for i in since_list(a[0], a[1]))
    if what == "escalated":                      # SINCE ALERT
        return any(i["escalated_at"] for i in since_list(a[0], a[1]))
    if what == "resolved":                       # SINCE ALERT
        found = since_list(a[0], a[1])
        return bool(found) and all(i["status"] == "resolved" for i in found)
    if what == "pm":                             # SINCE ALERT TEXT
        for i in since_list(a[0], a[1]):
            if a[2] in call("GET", f"{ONCALL}/api/incidents/{i['id']}/postmortem")[1]:
                return True
        return False
    if what in ("am-suppressed", "am-active"):   # ALERT [silenced|inhibited]
        for al in am_alerts():
            if al["labels"].get("alertname") != a[0]:
                continue
            st = al["status"]
            if what == "am-active":
                return st["state"] == "active"
            return st["state"] == "suppressed" and bool(st["silencedBy" if a[1] == "silenced" else "inhibitedBy"])
        return False
    sys.exit(__doc__)


def pick(ref):
    items = incidents()
    if ref == "latest":
        return items[-1]["id"] if items else None
    return ref


def main(argv):
    if not argv:
        sys.exit(__doc__)
    cmd, a = argv[0], argv[1:]
    try:
        if cmd == "check":
            return 0 if check(a[0], a[1:]) else 1
        if cmd == "fault":
            code, body = call("POST", f"{APP}/_fault?mode={a[0]}")
            return print(f"fault mode: {a[0]}") if code == 200 else sys.exit(body)
        if cmd == "automation":
            call("POST", ONCALL + "/api/settings", {"auto_remediation": a[0] == "on"})
            return print(f"automatic runbooks: {a[0]}")
        if cmd == "ack":
            inc = pick(a[0])
            code, body = call("POST", f"{ONCALL}/api/incidents/{inc}/ack", {"who": a[1]})
            return print(f"{inc} acknowledged by {a[1]}") if code == 200 else sys.exit(body)
        if cmd == "postmortem":
            return print(call("GET", f"{ONCALL}/api/incidents/{pick(a[0])}/postmortem")[1])
        if cmd == "last-id":
            return print(max([i["n"] for i in incidents()], default=0))
        if cmd == "summary":
            for i in since_list(a[0], a[1]):
                ack = f"acknowledged in {i['acked_at'] - i['created']:.0f}s by {i['acked_by']}" if i["acked_at"] else "not acknowledged"
                res = f"resolved in {i['resolved_at'] - i['created']:.0f}s" if i["resolved_at"] else "still open"
                print(f"{i['id']} {i['alert']}: {ack}, {res}")
            return
        if cmd == "silence":
            now = time.time()
            body = {"matchers": [{"name": "alertname", "value": a[0], "isRegex": False, "isEqual": True}], "startsAt": iso(now - 5),
                    "endsAt": iso(now + 60 * float(a[1])), "createdBy": "ir.py", "comment": "demo maintenance window"}
            code, text = call("POST", AM + "/api/v2/silences", body)
            return print(f"silence created for {a[0]} ({a[1]} min)") if code == 200 else sys.exit(text)
        if cmd == "unsilence":
            for s in jget(AM + "/api/v2/silences"):
                if s["createdBy"] == "ir.py" and s["status"]["state"] == "active":
                    call("DELETE", f"{AM}/api/v2/silence/{s['id']}")
            return print("silences removed")
        if cmd == "status":
            state = jget(ONCALL + "/api/state")
            print(f"on-call: primary {state['schedule']['primary']}, secondary {state['schedule']['secondary']}, "
                  f"ack timeout {state['ack_timeout']:g}s, automatic runbooks {'on' if state['settings']['auto_remediation'] else 'off'}")
            for i in state["incidents"]:
                print(f"{i['id']}  {i['alert']:<14} {i['status']:<13} assignee {i['assignee']}")
            if isinstance(state["alertmanager"], list):
                for al in state["alertmanager"]:
                    print(f"alertmanager: {al['alert']} {'silenced' if al['silenced'] else 'inhibited' if al['inhibited'] else al['state']}")
            return
    except (OSError, ValueError, KeyError, IndexError, StopIteration) as exc:
        if cmd == "check":
            return 2
        sys.exit(f"{cmd} failed: {exc}. Is the stack running? ./scripts/up.sh")
    sys.exit(__doc__)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
