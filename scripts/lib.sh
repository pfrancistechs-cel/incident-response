#!/usr/bin/env bash
# Shared helpers. Sourced by the other scripts, not run directly.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

# Use plain docker if the current user may talk to the daemon, otherwise sudo.
if docker info >/dev/null 2>&1; then DOCKER="docker"; else DOCKER="sudo docker"; fi
DC="$DOCKER compose"

APP_URL="${APP_URL:-http://127.0.0.1:8080}"
ONCALL_URL="${ONCALL_URL:-http://127.0.0.1:8081}"
AM_URL="${AM_URL:-http://127.0.0.1:9093}"
PROM_URL="${PROM_URL:-http://127.0.0.1:9090}"
STATE_DIR="${STATE_DIR:-$ROOT/state}"
IR="$ROOT/scripts/ir.py"
EXPECTED_CONTAINERS=5                               # app, traffic, prometheus, alertmanager, oncall
export APP_URL ONCALL_URL AM_URL PROM_URL STATE_DIR DOCKER

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok()   { printf '  \033[1;32mok\033[0m   %s\n' "$*"; }
fail() { printf '  \033[1;31mFAIL\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }
say()  { printf '    %s\n' "$*"; }

ensure_env() { [ -f "$ROOT/.env" ] || cp "$ROOT/.env.example" "$ROOT/.env"; set -a; . "$ROOT/.env"; set +a; }
ensure_env

public_ip() { # EC2 metadata (IMDSv2), then a public what-is-my-ip fallback
  local tok ip=""
  tok="$(curl -fsS -m 2 -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' 2>/dev/null || true)"
  if [ -n "$tok" ]; then
    ip="$(curl -fsS -m 2 -H "X-aws-ec2-metadata-token: $tok" http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || true)"
  fi
  if [ -z "$ip" ]; then ip="$(curl -fsS -m 4 https://checkip.amazonaws.com 2>/dev/null | tr -d '[:space:]' || true)"; fi
  echo "${ip:-<EC2-PUBLIC-IP>}"
}

http_code() { curl -s -o /dev/null -m 5 -w '%{http_code}' "$@" 2>/dev/null || true; }   # 000 = no connection

wait_until() { # SECONDS COMMAND...   (retries every second; the command is re-run each time)
  local secs="$1" i; shift
  for ((i = 0; i < secs; i++)); do "$@" >/dev/null 2>&1 && return 0; sleep 1; done
  return 1
}

# ---------------------------------------------------------------- conditions for wait_until (re-evaluated on every try)
oncall_up()   { [ "$(http_code "$ONCALL_URL/healthz")" = "200" ]; }
prom_ready()  { [ "$(http_code "$PROM_URL/-/ready")" = "200" ]; }
am_ready()    { [ "$(http_code "$AM_URL/-/ready")" = "200" ]; }
stack_ready() { "$IR" check ready; }                 # everything answers and the shop is receiving traffic
