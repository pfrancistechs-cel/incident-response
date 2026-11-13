# Incident Response and On-Call (Project 23)

Detect, page, act, learn. Prometheus detects a broken shop, **Alertmanager** groups, routes, silences and inhibits, an on-call service opens an incident, pages the primary, escalates to the secondary if nobody answers, runs the **runbook** automatically, and writes a **postmortem draft** from the timeline. This is the part Project 20 left out (no Alertmanager there).

```
traffic -> app :8080 --/metrics--> Prometheus --alerts--> Alertmanager --webhook--> oncall :8081
                ^                   (rules: HighErrorRate, HighLatency)  (group, silence,    |  incident, page, escalate,
                '------------ runbook: POST /_admin/reset-pool ---------- inhibit)          '- runbook, postmortem draft
```

| Scenario | What it proves |
|---|---|
| `page` | Alert, page, acknowledge, automatic runbook fix, resolve, postmortem draft |
| `escalate` | No runbook and no answer: the page escalates to the secondary; the postmortem notes it |
| `silence` | A maintenance window: the alert fires, nobody is paged; remove it and the page goes out |
| `inhibit` | Errors and slowness, one cause: `HighLatency` is inhibited, one incident |

Five containers: the app and traffic and the on-call service are `python:3.12-slim` with the code mounted in; Prometheus and Alertmanager are the official images. Nothing is built. Security group: 22, 8080, 8081 from My IP.

## Quick start
See **STEPS.md** (EC2 from scratch, copy-paste) and **DEMO.md** (8-minute script). `DIAGRAM_PROMPT.md` makes the architecture picture.

```bash
chmod +x scripts/*.sh scripts/*.py
./scripts/setup.sh && ./scripts/preflight.sh
./scripts/up.sh && ./scripts/smoke.sh && ./scripts/demo.sh gameday
./scripts/destroy.sh
```

## Honest limits
- Paging is simulated: the "pager" writes to a feed and the console. No SMS, phone call or app push. Real options: PagerDuty, Opsgenie, Grafana OnCall.
- The on-call schedule is a fixed primary and secondary; no rotations or time zones.
- The automatic runbook is one hard-coded fix (reset the pool). It hides the root cause, and the postmortem draft says so.
- Postmortem drafts are generated from the timeline; the root cause and impact sections stay `TODO` for humans.
- Demo timings are short (group_wait 2 s, ack timeout 15 s). Real values: 30 s, minutes, hours.
- No login on the console, the API or the fault-injection endpoint; the security group (My IP) is the only protection.
- Tests: the real app, on-call service, scripts, Prometheus 2.54.1 and Alertmanager 0.27.0 run as local processes with a stub `docker`. Real containers, image pulls, container networking, healthchecks and the `wget` healthchecks in the official images are verified only by `preflight.sh` on the instance.
