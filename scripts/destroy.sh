#!/usr/bin/env bash
# Removes everything this project created on the instance: the containers, the network, the incidents and postmortems, and .env.
# Usage: ./scripts/destroy.sh [--keep-env]
set -uo pipefail
. "$(dirname "$0")/lib.sh"

log "Stopping and removing the containers and the network"
$DC down -v --remove-orphans
rm -rf "$STATE_DIR"/*.json "$STATE_DIR"/*.tmp "$STATE_DIR"/postmortems
if [ "${1:-}" != "--keep-env" ]; then rm -f "$ROOT/.env"; fi

echo
echo "Cleanup done. (The images stay cached: sudo docker image prune -af removes them)"
echo "IMPORTANT: terminate the EC2 instance in the AWS console (EC2 > Instances >"
echo "Instance state > Terminate) so it stops costing money. Then delete the security group."
