#!/usr/bin/env bash
# Scripted incident-response tour. Usage: ./scripts/demo.sh [page|escalate|silence|inhibit|gameday]
# Keep the on-call console (http://<EC2-PUBLIC-IP>:8081/) open while it runs.
#   page      the shop breaks: alert, page, acknowledge, automatic runbook, resolve, postmortem draft
#   escalate  a problem with no runbook and nobody answering: the page escalates to the secondary
#   silence   a maintenance window: the alert fires but nobody is paged; remove it and the page goes out
#   inhibit   two alerts, one cause: only one incident
set -uo pipefail
. "$(dirname "$0")/lib.sh"

FAILURES=0
assert() { # LABEL COMMAND...
  local label="$1"; shift
  if "$@"; then ok "$label"; else fail "$label"; FAILURES=$((FAILURES + 1)); fi
}
finish() {
  if [ "$FAILURES" -eq 0 ]; then printf '\n\033[1;32mPASS\033[0m  %s\n\n' "$1"; else printf '\n\033[1;31mFAIL\033[0m  %s\n\n' "$1"; exit 1; fi
}
need_stack() { oncall_up && prom_ready && am_ready || die "The stack is not running. Run ./scripts/up.sh first."; }
start_clean() { # no fault, no silence, automatic runbooks on, nothing open; then remember where the incident numbers stand
  "$IR" automation on >/dev/null; "$IR" unsilence >/dev/null; "$IR" fault ok >/dev/null
  wait_until 150 "$IR" check quiet || die "the previous incident did not clear (see: ./scripts/ir.py status)"
  SINCE="$("$IR" last-id)"
}
SINCE=0

scenario_page() {
  log "SCENARIO page: the shop breaks (database connection pool exhausted)"
  start_clean
  "$IR" fault pool
  assert "an incident is opened for HighErrorRate"          wait_until 90 "$IR" check incident "$SINCE" HighErrorRate triggered acknowledged resolved
  assert "the primary on-call (alice) was paged"            "$IR" check paged "$SINCE" HighErrorRate alice
  "$IR" ack latest alice
  assert "alice's acknowledgement is recorded"              "$IR" check acked "$SINCE" HighErrorRate alice
  assert "the runbook ran by itself and verified the fix"   wait_until 40 "$IR" check runbook-ok "$SINCE" HighErrorRate
  assert "checkouts succeed again"                          wait_until 20 "$IR" check app-ok
  assert "Alertmanager resolved it and the incident closed" wait_until 120 "$IR" check resolved "$SINCE" HighErrorRate
  assert "the postmortem draft has a timeline"              "$IR" check pm "$SINCE" HighErrorRate "## Timeline"
  assert "it records the automatic fix"                     "$IR" check pm "$SINCE" HighErrorRate "runbook reset-pool"
  "$IR" summary "$SINCE" HighErrorRate | sed 's/^/    /'
  "$IR" postmortem latest | sed -n '1,22p' | sed 's/^/    > /'
  finish page
}

scenario_escalate() {
  log "SCENARIO escalate: slow checkouts, no runbook, nobody answers"
  start_clean
  "$IR" fault slow
  assert "an incident is opened for HighLatency"            wait_until 120 "$IR" check incident "$SINCE" HighLatency triggered acknowledged
  assert "the primary (alice) was paged"                    "$IR" check paged "$SINCE" HighLatency alice
  say "nobody acknowledges... waiting for the ack timeout"
  assert "the page escalates to the secondary (bob)"        wait_until 60 "$IR" check escalated "$SINCE" HighLatency
  assert "bob was paged"                                    "$IR" check paged "$SINCE" HighLatency bob
  "$IR" ack latest bob
  assert "bob's acknowledgement is recorded"                "$IR" check acked "$SINCE" HighLatency bob
  say "bob finds the cause and fixes it by hand"
  "$IR" fault ok
  assert "the incident resolves"                            wait_until 120 "$IR" check resolved "$SINCE" HighLatency
  assert "the postmortem notes the escalation"              "$IR" check pm "$SINCE" HighLatency "escalating"
  assert "the postmortem asks for a runbook"                "$IR" check pm "$SINCE" HighLatency "Write a runbook"
  "$IR" summary "$SINCE" HighLatency | sed 's/^/    /'
  finish escalate
}

scenario_silence() {
  log "SCENARIO silence: a maintenance window"
  start_clean
  "$IR" silence HighErrorRate 5
  "$IR" fault pool
  assert "Alertmanager holds the alert as silenced"         wait_until 90 "$IR" check am-suppressed HighErrorRate silenced
  sleep 8
  assert "nobody was paged: no incident exists"             "$IR" check no-incident "$SINCE" HighErrorRate
  assert "the shop is still failing (nothing fixed it)"     "$IR" check app-failing
  "$IR" unsilence
  assert "silence removed: the page goes out"               wait_until 60 "$IR" check incident "$SINCE" HighErrorRate triggered acknowledged resolved
  assert "the runbook fixes the shop"                       wait_until 40 "$IR" check runbook-ok "$SINCE" HighErrorRate
  assert "the incident closes"                              wait_until 120 "$IR" check resolved "$SINCE" HighErrorRate
  finish silence
}

scenario_inhibit() {
  log "SCENARIO inhibit: errors and slowness, one cause"
  start_clean
  "$IR" automation off
  "$IR" fault both
  assert "one incident for HighErrorRate"                   wait_until 90 "$IR" check incident "$SINCE" HighErrorRate triggered acknowledged escalated
  assert "HighLatency is firing but inhibited"              wait_until 60 "$IR" check am-suppressed HighLatency inhibited
  assert "no second incident, no second page"               "$IR" check incident-count "$SINCE" 1
  "$IR" automation on
  "$IR" fault ok
  assert "the incident closes"                              wait_until 120 "$IR" check resolved "$SINCE" HighErrorRate
  sleep 6
  assert "still exactly one incident"                       "$IR" check incident-count "$SINCE" 1
  finish inhibit
}

need_stack
case "${1:-gameday}" in
  page)     scenario_page ;;
  escalate) scenario_escalate ;;
  silence)  scenario_silence ;;
  inhibit)  scenario_inhibit ;;
  gameday)  scenario_page; scenario_escalate; scenario_silence; scenario_inhibit
            start_clean
            printf '\033[1;32mALL SCENARIOS PASSED\033[0m\n' ;;
  *) echo "Usage: $0 [page|escalate|silence|inhibit|gameday]" >&2; exit 2 ;;
esac
