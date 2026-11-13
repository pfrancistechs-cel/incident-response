import os
import re
import stat
import subprocess
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS = ["scripts/" + f for f in os.listdir(os.path.join(ROOT, "scripts")) if f.endswith(".sh")]
EXEC = SCRIPTS + ["scripts/ir.py"]


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


class Repo(unittest.TestCase):
    def test_scripts_parse_and_are_executable(self):
        for rel in EXEC:
            path = os.path.join(ROOT, rel)
            self.assertTrue(os.stat(path).st_mode & stat.S_IXUSR, rel + " is not executable")
            if rel.endswith(".sh"):
                self.assertEqual(subprocess.run(["bash", "-n", path]).returncode, 0, rel)

    def test_python_files_compile(self):
        for rel in ("app/app.py", "app/traffic.py", "oncall/oncall.py", "scripts/ir.py"):
            compile(read(rel), rel, "exec")

    def test_required_files(self):
        for rel in ("docker-compose.yml", ".env.example", ".gitignore", ".gitattributes", "state/.gitkeep", "prometheus/prometheus.yml",
                    "prometheus/rules.yml", "alertmanager/alertmanager.yml", "README.md", "STEPS.md", "DEMO.md", "DIAGRAM_PROMPT.md"):
            self.assertTrue(os.path.exists(os.path.join(ROOT, rel)), rel)

    def test_wait_until_is_never_given_a_command_substitution(self):
        for rel in SCRIPTS:
            for n, line in enumerate(read(rel).splitlines(), 1):
                if re.search(r"wait_until\s+\d+\s.*\$\(", line) and not line.lstrip().startswith("#"):
                    self.fail(f"{rel}:{n} passes $(...) to wait_until: {line.strip()}")

    def test_no_pipe_into_grep_q(self):
        for rel in SCRIPTS:
            self.assertNotRegex(read(rel), r"\|\s*grep -q", rel)

    def test_helpers_used_by_a_script_are_defined(self):
        lib = read("scripts/lib.sh")
        for rel in SCRIPTS:
            text = read(rel)
            for fn in re.findall(r"wait_until\s+\d+\s+(\w+)", text):
                self.assertTrue(re.search(rf"^{fn}\(\)", lib + text, re.M), f"{rel} uses undefined function {fn}")

    def test_only_the_intended_ports_are_published(self):
        ports = sorted(re.findall(r'ports:\s*\["([^"]+)"\]', read("docker-compose.yml")))
        self.assertEqual(ports, ["127.0.0.1:9090:9090", "127.0.0.1:9093:9093", "8080:8080", "8081:8081"])

    def test_images_are_pinned_and_nothing_is_built(self):
        compose = read("docker-compose.yml") + read(".env.example")
        self.assertNotIn("build:", compose)
        self.assertNotIn(":latest", compose)
        for image in ("python:3.12-slim", "prom/prometheus:v2.54.1", "prom/alertmanager:v0.27.0"):
            self.assertIn(image, compose)
        self.assertEqual(len(re.findall(r"container_name:", read("docker-compose.yml"))), 5)
        self.assertIn("EXPECTED_CONTAINERS=5", read("scripts/lib.sh"))

    def test_alert_rules_are_wired_to_the_runbook_and_the_inhibition(self):
        rules, am = read("prometheus/rules.yml"), read("alertmanager/alertmanager.yml")
        self.assertIn("runbook: reset-pool", rules)
        self.assertIn("http://oncall:9094/alert", am)
        self.assertIn("send_resolved: true", am)
        self.assertIn('alertname="HighErrorRate"', am)
        self.assertIn('alertname="HighLatency"', am)

    def test_every_script_run_by_hand_is_documented(self):
        steps = read("STEPS.md")
        self.assertIn("chmod +x scripts/*.sh scripts/*.py", steps)
        for cmd in ("setup.sh", "preflight.sh", "up.sh", "smoke.sh", "demo.sh gameday", "destroy.sh", "All 12 checks passed.", "ir.py"):
            self.assertIn(cmd, steps)
        for port in ("22", "8080", "8081"):
            self.assertIn(port, steps)

    def test_demo_doc_is_brief(self):
        text = read("DEMO.md")
        self.assertLess(len(text.split()), 450)
        self.assertLess(len(text.splitlines()), 40)


if __name__ == "__main__":
    unittest.main()
