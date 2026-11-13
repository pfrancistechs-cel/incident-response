"""Test helper: the real app, traffic generator, on-call service, Prometheus and Alertmanager as local processes, with the
project's own rules and Alertmanager config (only hostnames and ports rewritten). docker is a tiny stub."""
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TOOLS = ("prometheus", "alertmanager")
AVAILABLE = all(shutil.which(t) for t in TOOLS)
SKIP_REASON = "needs prometheus and alertmanager in PATH (the demo instance runs them as containers)"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Stack:
    def __init__(self, ack_timeout="8", rate="20"):
        self.tmp = tempfile.mkdtemp(prefix="ir")
        for d in ("state", "bin", "prom", "am"):
            os.makedirs(os.path.join(self.tmp, d))
        self.state = os.path.join(self.tmp, "state")
        self.p = {k: free_port() for k in ("app", "console", "webhook", "prom", "am", "amcluster")}
        self.ack_timeout, self.rate = ack_timeout, rate
        stub = os.path.join(self.tmp, "bin", "docker")
        with open(stub, "w") as fh:
            fh.write('#!/bin/sh\ncase "$1 $2" in\n  "compose ps") for i in 1 2 3 4 5; do echo c$i; done ;;\nesac\nexit 0\n')
        os.chmod(stub, 0o755)
        self.urls = {"APP_URL": f"http://127.0.0.1:{self.p['app']}", "ONCALL_URL": f"http://127.0.0.1:{self.p['console']}",
                     "AM_URL": f"http://127.0.0.1:{self.p['am']}", "PROM_URL": f"http://127.0.0.1:{self.p['prom']}"}
        self.env = dict(os.environ, PYTHONUNBUFFERED="1", no_proxy="127.0.0.1,localhost", NO_PROXY="127.0.0.1,localhost",
                        PATH=os.path.join(self.tmp, "bin") + os.pathsep + os.environ["PATH"], STATE_DIR=self.state, **self.urls)
        for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            self.env.pop(var, None)
        self.procs = []

    def spawn(self, argv, **env):
        self.procs.append(subprocess.Popen(argv, env={**self.env, **env}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                           start_new_session=True))

    def wait(self, url, secs=30):
        end = time.time() + secs
        while True:
            try:
                OPENER.open(url, timeout=2).read()
                return
            except Exception:
                if time.time() > end:
                    raise RuntimeError("not ready: " + url)
                time.sleep(0.2)

    def start(self):
        py = sys.executable
        shutil.copy(os.path.join(ROOT, "prometheus", "rules.yml"), os.path.join(self.tmp, "rules.yml"))
        cfg = open(os.path.join(ROOT, "prometheus", "prometheus.yml")).read()
        cfg = cfg.replace("/etc/prometheus/rules.yml", os.path.join(self.tmp, "rules.yml")).replace("alertmanager:9093", f"127.0.0.1:{self.p['am']}")
        cfg = cfg.replace("app:8080", f"127.0.0.1:{self.p['app']}")
        open(os.path.join(self.tmp, "prometheus.yml"), "w").write(cfg)
        am = open(os.path.join(ROOT, "alertmanager", "alertmanager.yml")).read().replace("oncall:9094", f"127.0.0.1:{self.p['webhook']}")
        open(os.path.join(self.tmp, "alertmanager.yml"), "w").write(am)
        self.spawn([py, os.path.join(ROOT, "app", "app.py")], PORT=str(self.p["app"]))
        self.spawn([py, os.path.join(ROOT, "oncall", "oncall.py")], API_PORT=str(self.p["webhook"]), CONSOLE_PORT=str(self.p["console"]),
                   APP_URL=self.urls["APP_URL"], AM_URL=self.urls["AM_URL"], ACK_TIMEOUT=self.ack_timeout)
        self.spawn(["alertmanager", f"--config.file={self.tmp}/alertmanager.yml", f"--storage.path={self.tmp}/am",
                    f"--web.listen-address=127.0.0.1:{self.p['am']}", f"--cluster.listen-address="])
        self.spawn(["prometheus", f"--config.file={self.tmp}/prometheus.yml", f"--storage.tsdb.path={self.tmp}/prom",
                    f"--web.listen-address=127.0.0.1:{self.p['prom']}", "--storage.tsdb.retention.time=1h"])
        for url in (self.urls["APP_URL"] + "/healthz", self.urls["ONCALL_URL"] + "/healthz", self.urls["AM_URL"] + "/-/ready",
                    self.urls["PROM_URL"] + "/-/ready"):
            self.wait(url, 40)
        self.spawn([py, os.path.join(ROOT, "app", "traffic.py")], TARGET=self.urls["APP_URL"], RATE=self.rate)
        return self

    def script(self, *args, timeout=400, **env):
        r = subprocess.run([os.path.join(ROOT, "scripts", args[0]), *args[1:]], env={**self.env, **env}, capture_output=True,
                           text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        return r.returncode, r.stdout + r.stderr

    def get(self, url):
        return OPENER.open(url, timeout=5).read().decode()

    def stop(self):
        for proc in self.procs:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
        time.sleep(0.3)
