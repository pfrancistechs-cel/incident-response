"""The real scripts, app, traffic generator, on-call service, Prometheus and Alertmanager; docker is a stub."""
import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import stack  # noqa: E402

ANSI = re.compile(r"\x1b\[[0-9;]*m")


@unittest.skipUnless(stack.AVAILABLE, stack.SKIP_REASON)
class Scenarios(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = stack.Stack().start()

    @classmethod
    def tearDownClass(cls):
        cls.s.stop()

    def scenario(self, name):
        code, out = self.s.script("demo.sh", name)
        out = ANSI.sub("", out)
        self.assertEqual(code, 0, out)
        self.assertIn("PASS  " + name, out)
        return out

    def test_1_smoke(self):
        code, out = self.s.script("smoke.sh")
        self.assertEqual(code, 0, ANSI.sub("", out))
        self.assertIn("All 12 checks passed.", out)

    def test_2_page(self):
        out = self.scenario("page")
        self.assertIn("## Timeline", out)
        self.assertIn("acknowledged in", out)

    def test_3_escalate(self):
        out = self.scenario("escalate")
        self.assertIn("acknowledged in", out)

    def test_4_silence(self):
        self.scenario("silence")

    def test_5_inhibit(self):
        self.scenario("inhibit")

    def test_6_console_and_postmortem_files(self):
        state = json.loads(self.s.get(self.s.urls["ONCALL_URL"] + "/api/state"))
        statuses = {i["status"] for i in state["incidents"]}
        self.assertEqual(statuses, {"resolved"})
        pm_dir = os.path.join(self.s.state, "postmortems")
        files = sorted(os.listdir(pm_dir))
        self.assertGreaterEqual(len(files), 4)
        text = open(os.path.join(pm_dir, files[0])).read()
        self.assertIn("## Action items", text)
        self.assertIn("On-call", self.s.get(self.s.urls["ONCALL_URL"] + "/"))

    def test_7_gameday_runs_everything_in_order(self):
        code, out = self.s.script("demo.sh", "gameday", timeout=900)
        out = ANSI.sub("", out)
        self.assertEqual(code, 0, out)
        order = [out.index("PASS  " + n) for n in ("page", "escalate", "silence", "inhibit")]
        self.assertEqual(order, sorted(order))
        self.assertIn("ALL SCENARIOS PASSED", out)


if __name__ == "__main__":
    unittest.main()
