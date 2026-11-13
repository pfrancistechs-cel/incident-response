"""No docker, no Prometheus needed (promtool/amtool tests skip without them): the app, the on-call service, the alert rules."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(__file__))
import stack  # noqa: E402

ROOT = stack.ROOT
OPENER = stack.OPENER


def call(method, url, body=None):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else (b"" if method == "POST" else None),
                                 headers={"Content-Type": "application/json"})
    try:
        with OPENER.open(req, timeout=5) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def spawn(script, **env):
    proc = subprocess.Popen([sys.executable, os.path.join(ROOT, script)], env=dict(os.environ, no_proxy="127.0.0.1", **env),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return proc


def wait(url):
    for _ in range(60):
        try:
            OPENER.open(url, timeout=1).read()
            return
        except Exception:
            time.sleep(0.1)
    raise RuntimeError(url)


class App(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = stack.free_port()
        cls.proc = spawn("app/app.py", PORT=str(cls.port), SLOW_SECONDS="0.3")
        cls.url = f"http://127.0.0.1:{cls.port}"
        wait(cls.url + "/healthz")

    @classmethod
    def tearDownClass(cls):
        cls.proc.kill()

    def mode(self, m):
        self.assertEqual(call("POST", f"{self.url}/_fault?mode={m}")[0], 200)

    def test_1_modes(self):
        self.mode("ok")
        self.assertEqual(call("GET", self.url + "/checkout")[0], 200)
        self.mode("pool")
        self.assertEqual(call("GET", self.url + "/checkout")[0], 500)
        self.mode("slow")
        start = time.time()
        self.assertEqual(call("GET", self.url + "/checkout")[0], 200)
        self.assertGreater(time.time() - start, 0.25)
        self.mode("both")
        start = time.time()
        self.assertEqual(call("GET", self.url + "/checkout")[0], 500)
        self.assertGreater(time.time() - start, 0.25)
        self.assertEqual(call("POST", self.url + "/_fault?mode=nope")[0], 400)

    def test_2_reset_pool_fixes_the_pool_but_not_slowness(self):
        for before, after in (("pool", "ok"), ("both", "slow"), ("slow", "slow"), ("ok", "ok")):
            self.mode(before)
            self.assertEqual(json.loads(call("POST", self.url + "/_admin/reset-pool")[1])["after"], after)
            self.assertEqual(json.loads(call("GET", self.url + "/_stats")[1])["mode"], after)

    def test_3_metrics(self):
        self.mode("ok")
        call("GET", self.url + "/checkout")
        self.mode("pool")
        call("GET", self.url + "/checkout")
        text = call("GET", self.url + "/metrics")[1]
        self.assertIn('ir_requests_total{code="200"}', text)
        self.assertIn('ir_requests_total{code="500"}', text)
        self.assertIn("ir_request_seconds_sum", text)
        self.assertIn("ir_request_seconds_count", text)
        self.assertIn('ir_fault_mode{mode="pool"} 1', text)
        self.mode("ok")


class FakeApp(BaseHTTPRequestHandler):
    resets = []

    def log_message(self, *a):
        pass

    def reply(self, body):
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self.reply({"mode": "pool"} if self.path.startswith("/_stats") else {"ok": True})

    def do_POST(self):
        FakeApp.resets.append(self.path)
        self.reply({"before": "pool", "after": "ok"})


def alert(name, status="firing", runbook=None):
    labels = {"alertname": name, "severity": "page", "service": "shop"}
    if runbook:
        labels["runbook"] = runbook
    return {"alerts": [{"status": status, "labels": labels, "annotations": {"summary": f"{name} summary"},
                        "startsAt": "2026-11-12T10:00:00.123456789Z"}]}


class OnCall(unittest.TestCase):
    ACK = 1.5

    def setUp(self):
        FakeApp.resets.clear()
        self.fake = ThreadingHTTPServer(("127.0.0.1", 0), FakeApp)
        threading.Thread(target=self.fake.serve_forever, daemon=True).start()
        self.tmp = tempfile.mkdtemp()
        self.start()

    def start(self):
        self.api, self.console = stack.free_port(), stack.free_port()
        self.proc = spawn("oncall/oncall.py", API_PORT=str(self.api), CONSOLE_PORT=str(self.console), STATE_DIR=self.tmp,
                          APP_URL=f"http://127.0.0.1:{self.fake.server_port}", AM_URL="http://127.0.0.1:1", ACK_TIMEOUT=str(self.ACK))
        self.url = f"http://127.0.0.1:{self.console}"
        wait(self.url + "/healthz")

    def tearDown(self):
        self.proc.kill()
        self.fake.shutdown()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def incidents(self):
        return json.loads(call("GET", self.url + "/api/incidents")[1])

    def fire(self, name="HighLatency", **kw):
        call("POST", f"http://127.0.0.1:{self.api}/alert", alert(name, **kw))

    def test_firing_alert_opens_an_incident_and_pages_the_primary(self):
        self.fire()
        inc = self.incidents()[0]
        self.assertEqual((inc["id"], inc["alert"], inc["status"], inc["assignee"]), ("INC-0001", "HighLatency", "triggered", "alice"))
        self.assertIn("paged alice (primary)", [e["detail"] for e in inc["timeline"]])

    def test_a_repeated_notification_does_not_open_a_second_incident(self):
        self.fire()
        self.fire()
        self.assertEqual(len(self.incidents()), 1)

    def test_different_alerts_are_different_incidents(self):
        self.fire("HighLatency")
        self.fire("HighErrorRate")
        self.assertEqual([i["alert"] for i in self.incidents()], ["HighLatency", "HighErrorRate"])

    def test_ack_stops_the_escalation(self):
        self.fire()
        self.assertEqual(call("POST", self.url + "/api/incidents/INC-0001/ack", {"who": "alice"})[0], 200)
        time.sleep(self.ACK + 1)
        inc = self.incidents()[0]
        self.assertEqual((inc["status"], inc["acked_by"], inc["escalated_at"]), ("acknowledged", "alice", None))

    def test_no_ack_escalates_to_the_secondary(self):
        self.fire()
        time.sleep(self.ACK + 1.2)
        inc = self.incidents()[0]
        self.assertEqual((inc["status"], inc["assignee"]), ("escalated", "bob"))
        self.assertIn("paged bob (secondary)", [e["detail"] for e in inc["timeline"]])

    def test_resolved_closes_the_incident_and_writes_the_postmortem(self):
        self.fire()
        call("POST", self.url + "/api/incidents/INC-0001/ack", {"who": "alice"})
        self.fire(status="resolved")
        inc = self.incidents()[0]
        self.assertEqual(inc["status"], "resolved")
        pm = call("GET", self.url + "/api/incidents/INC-0001/postmortem")[1]
        for section in ("## Summary", "## Metrics", "## Timeline", "## Root cause", "## What went well", "## What went wrong", "## Action items"):
            self.assertIn(section, pm)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "postmortems", "INC-0001.md")))
        self.assertIn("Write a runbook for HighLatency", pm)            # no runbook label: it asks for one

    def test_a_new_firing_after_resolution_is_a_new_incident(self):
        self.fire()
        self.fire(status="resolved")
        self.fire()
        self.assertEqual([i["status"] for i in self.incidents()], ["resolved", "triggered"])

    def test_postmortem_of_an_escalated_incident_says_so(self):
        self.fire()
        time.sleep(self.ACK + 1.2)
        self.fire(status="resolved")
        pm = call("GET", self.url + "/api/incidents/INC-0001/postmortem")[1]
        self.assertIn("escalating", pm)
        self.assertIn("Check why the primary did not acknowledge", pm)

    def test_ack_errors(self):
        self.assertEqual(call("POST", self.url + "/api/incidents/INC-0099/ack", {"who": "x"})[0], 404)
        self.fire()
        self.fire(status="resolved")
        self.assertEqual(call("POST", self.url + "/api/incidents/INC-0001/ack", {"who": "x"})[0], 409)

    def test_runbook_runs_automatically_when_the_alert_has_one(self):
        self.fire("HighErrorRate", runbook="reset-pool")
        time.sleep(4.5)
        inc = self.incidents()[0]
        self.assertEqual(FakeApp.resets, ["/_admin/reset-pool"])
        self.assertEqual(inc["mitigated_by"], "runbook reset-pool")
        self.assertIn("reset-pool finished: OK", [e["detail"] for e in inc["timeline"]])

    def test_runbook_does_not_run_when_automation_is_off_or_there_is_none(self):
        call("POST", self.url + "/api/settings", {"auto_remediation": False})
        self.fire("HighErrorRate", runbook="reset-pool")
        self.fire("HighLatency")
        time.sleep(3)
        self.assertEqual(FakeApp.resets, [])
        self.assertTrue(all(i["mitigated_by"] is None for i in self.incidents()))

    def test_schedule_change_applies_to_the_next_page(self):
        call("POST", self.url + "/api/schedule", {"primary": "carol"})
        self.fire()
        self.assertEqual(self.incidents()[0]["assignee"], "carol")

    def test_incidents_survive_a_restart_and_numbering_continues(self):
        self.fire()
        self.proc.kill()
        self.proc.wait()
        self.start()
        self.assertEqual(len(self.incidents()), 1)
        self.fire("HighErrorRate")
        self.assertEqual([i["id"] for i in self.incidents()], ["INC-0001", "INC-0002"])

    def test_console_and_state(self):
        self.fire()
        self.assertIn("On-call", call("GET", self.url + "/")[1])
        state = json.loads(call("GET", self.url + "/api/state")[1])
        self.assertEqual(state["pages"][-1]["who"], "alice")
        self.assertIn("error", state["alertmanager"])                  # Alertmanager is unreachable here: reported, not a crash


@unittest.skipUnless(shutil.which("promtool"), "promtool not installed")
class Rules(unittest.TestCase):
    def run_test(self, errors_per_step, expect_firing, alert="HighErrorRate", latency=None):
        series = f"""
      - series: 'ir_requests_total{{code="200"}}'
        values: '0+100x30'
      - series: 'ir_requests_total{{code="500"}}'
        values: '0+{errors_per_step}x30'
      - series: 'ir_request_seconds_sum'
        values: '0+{latency if latency is not None else 5}x30'
      - series: 'ir_request_seconds_count'
        values: '0+100x30'"""
        exp = ("exp_alerts:\n          - exp_labels: {severity: page, service: shop%s}\n            exp_annotations: {summary: \"%s\", runbook: \"%s\"}"
               % (", runbook: reset-pool" if alert == "HighErrorRate" else "",
                  "More than 5% of checkouts are failing" if alert == "HighErrorRate" else "Checkouts take more than 0.5 seconds on average",
                  "reset-pool: clear the connection pool, then verify checkouts succeed" if alert == "HighErrorRate" else "none: a human has to find the cause")
               ) if expect_firing else "exp_alerts: []"
        doc = f"""rule_files: ["{ROOT}/prometheus/rules.yml"]
evaluation_interval: 5s
tests:
  - interval: 5s
    input_series:{series}
    alert_rule_test:
      - eval_time: 120s
        alertname: {alert}
        {exp}
"""
        path = os.path.join(tempfile.mkdtemp(), "t.yml")
        open(path, "w").write(doc)
        r = subprocess.run(["promtool", "test", "rules", path], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_rules_are_valid(self):
        self.assertEqual(subprocess.run(["promtool", "check", "rules", os.path.join(ROOT, "prometheus", "rules.yml")], capture_output=True).returncode, 0)

    def test_error_alert_fires_on_sustained_errors_and_not_on_a_trickle(self):
        self.run_test(50, True)
        self.run_test(2, False)

    def test_latency_alert_fires_when_the_average_is_over_half_a_second(self):
        self.run_test(0, True, alert="HighLatency", latency=80)          # 0.8 s per request
        self.run_test(0, False, alert="HighLatency", latency=5)          # 0.05 s


@unittest.skipUnless(shutil.which("amtool"), "amtool not installed")
class AlertmanagerConfig(unittest.TestCase):
    def test_config_is_valid_and_routes_to_the_webhook(self):
        path = os.path.join(ROOT, "alertmanager", "alertmanager.yml")
        self.assertEqual(subprocess.run(["amtool", "check-config", path], capture_output=True).returncode, 0)
        out = subprocess.run(["amtool", "config", "routes", "test", "--config.file", path, "alertname=HighLatency", "service=shop"],
                             capture_output=True, text=True)
        self.assertEqual(out.stdout.strip(), "oncall")


if __name__ == "__main__":
    unittest.main()
