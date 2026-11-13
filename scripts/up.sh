#!/usr/bin/env bash
# Starts the shop, traffic, Prometheus, Alertmanager and the on-call service, and waits until everything answers.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
mkdir -p "$STATE_DIR"; chmod 777 "$STATE_DIR"        # the containers run as an unprivileged user and write here

log "Starting the stack (first start pulls three images, 1-3 minutes; nothing is built)"
$DC up -d

log "Waiting for every part to answer"
wait_until 90 oncall_up  && ok "the on-call service answers on $ONCALL_URL" || die "Run: $DOCKER logs --tail 30 ir-oncall"
wait_until 90 prom_ready && ok "Prometheus is ready" || die "Run: $DOCKER logs --tail 30 ir-prometheus"
wait_until 90 am_ready   && ok "Alertmanager is ready" || die "Run: $DOCKER logs --tail 30 ir-alertmanager"
wait_until 60 stack_ready && ok "the shop is receiving traffic" || die "Run: $DOCKER logs --tail 30 ir-app ir-traffic"
wait_until 60 "$IR" check prom-target && ok "Prometheus scrapes the shop" || die "Run: $DOCKER logs --tail 30 ir-prometheus"

IP="$(public_ip)"
cat <<EOF

Ready. Open these in your browser (from the laptop allowed by the security group):
  On-call console : http://${IP}:8081/    <- keep it open
  The shop        : http://${IP}:8080/checkout

Next: ./scripts/smoke.sh   then   ./scripts/demo.sh gameday
By hand: ./scripts/ir.py status
EOF
