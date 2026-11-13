#!/usr/bin/env bash
# Health check of the running stack (run ./scripts/up.sh first). Ends with "All 12 checks passed."
set -uo pipefail
. "$(dirname "$0")/lib.sh"

TOTAL=0; BAD=0
check() { # LABEL COMMAND...
  local label="$1"; shift
  TOTAL=$((TOTAL + 1))
  if "$@" >/dev/null 2>&1; then ok "$label"; else fail "$label"; BAD=$((BAD + 1)); fi
}
five_running() { [ "$($DC ps --status running -q | wc -l | tr -d ' ')" = "$EXPECTED_CONTAINERS" ]; }

check "Docker daemon reachable"                              $DOCKER info
check "all 5 containers are running"                         five_running
check "the on-call service answers"                          oncall_up
check "the on-call console page is served"                   "$IR" check page-served
check "Prometheus is ready"                                  prom_ready
check "Alertmanager is ready"                                am_ready
check "Prometheus loaded both alert rules"                   "$IR" check prom-rules
check "Prometheus scrapes the shop"                          wait_until 30 "$IR" check prom-target
check "Prometheus is connected to Alertmanager"              wait_until 30 "$IR" check prom-am
check "the on-call service can reach Alertmanager"           "$IR" check oncall-sees-am
check "the shop is receiving traffic"                        stack_ready
check "no fault is injected in the shop"                     "$IR" check app-ok

echo
if [ "$BAD" -eq 0 ]; then
  printf '\033[1;32mAll %s checks passed.\033[0m\n' "$TOTAL"
else
  printf '\033[1;31m%s of %s checks failed.\033[0m See the troubleshooting table in STEPS.md.\n' "$BAD" "$TOTAL" >&2
  exit 1
fi
