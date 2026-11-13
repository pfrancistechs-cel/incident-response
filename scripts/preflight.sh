#!/usr/bin/env bash
# Run this BEFORE the demo. It starts the stack, runs the smoke test, then every demo scenario once (about 6 minutes), so a
# failure or a slow image pull shows up now. Everything is left running; stop it with ./scripts/destroy.sh only AFTER the demo.
set -uo pipefail
. "$(dirname "$0")/lib.sh"

FAILS=0
check() { # LABEL COMMAND...
  local label="$1"; shift
  if "$@" >/dev/null 2>&1; then ok "$label"; else fail "$label"; FAILS=$((FAILS + 1)); fi
}
pull_with_retry() { # IMAGE
  local i
  for i in 1 2 3; do $DOCKER pull "$1" >/dev/null 2>&1 && return 0; sleep 3; done
  return 1
}
# Docker Hub's /v2/ endpoint answers HTTP 401 (login required) when it is reachable.
# So 200 and 401 both mean "reachable". Do not add curl -f here.
hub_reachable() {
  local code
  code="$(curl -s -o /dev/null -m 10 -w '%{http_code}' https://registry-1.docker.io/v2/ || true)"
  [ "$code" = "200" ] || [ "$code" = "401" ]
}
disk_ok()   { [ "$(df -BG --output=avail / | tail -n1 | tr -dc '0-9')" -ge 4 ]; }
mem_ok()    { [ "$(free -m | awk '/^Mem:/{print $2}')" -ge 1500 ]; }
port_free() { ! python3 -c 'import socket, sys; s = socket.socket(); s.settimeout(1); sys.exit(0 if s.connect_ex(("127.0.0.1", int(sys.argv[1]))) == 0 else 1)' "$1"; }
stack_up()  { "$ROOT/scripts/up.sh"; }
all_tests() { PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests; }

log "System checks"
check "Docker daemon reachable"                  $DOCKER info
check "Docker Compose v2 installed"              $DC version
check "python3 is installed"                     python3 --version
check "At least 4 GB free disk"                  disk_ok
check "At least 1.5 GB RAM"                      mem_ok
if [ "$($DC ps -q | wc -l | tr -d ' ')" -gt 0 ]; then
  ok "Stack already running (port checks skipped)"
else
  for port in 8080 8081 9090 9093; do
    check "Port $port is free"                   port_free "$port"
  done
fi
check "Docker Hub reachable (HTTP 200 or 401)"   hub_reachable

log "Pulling images (up to 3 tries each)"
check "Pull ${PYTHON_IMAGE:-python:3.12-slim}"   pull_with_retry "${PYTHON_IMAGE:-python:3.12-slim}"
check "Pull ${PROMETHEUS_IMAGE:-prom/prometheus:v2.54.1}" pull_with_retry "${PROMETHEUS_IMAGE:-prom/prometheus:v2.54.1}"
check "Pull ${ALERTMANAGER_IMAGE:-prom/alertmanager:v0.27.0}" pull_with_retry "${ALERTMANAGER_IMAGE:-prom/alertmanager:v0.27.0}"

log "Starting everything"
check "up.sh: the stack starts and answers"      stack_up
check "smoke.sh: all 12 checks pass"             "$ROOT/scripts/smoke.sh"
log "The real scenarios, once (about 6 minutes)"
check "demo.sh gameday: page, escalate, silence, inhibit" "$ROOT/scripts/demo.sh" gameday

log "Tests"
check "Unit tests and repo tests"                all_tests

echo
if [ "$FAILS" -eq 0 ]; then
  echo "Preflight passed. The whole stack works on this instance. Next: ./scripts/demo.sh gameday"
  exit 0
fi
echo "Preflight found $FAILS problem(s). See the troubleshooting table in STEPS.md." >&2
echo "Pull failed? Run 'sudo docker login' with a free Docker Hub account." >&2
exit 1
