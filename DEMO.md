# Demo: Incident Response and On-Call (about 8 minutes)

Open `http://<EC2-PUBLIC-IP>:8081/` (the on-call console) and keep it visible: incidents, timeline, pager feed, Alertmanager.

**1. The idea (30 s).** "Prometheus **detects**, Alertmanager **routes**, a pager **wakes someone**, a runbook **acts**, a postmortem **learns**."

**2. A page, an auto-fix, a postmortem (2 min).**
```bash
./scripts/demo.sh page
```
The shop's connection pool is exhausted. Alert fires, alice is paged, she acknowledges, the **runbook resets the pool by itself**, the alert resolves, and a **postmortem draft** is written from the timeline.

**3. Escalation (2 min).**
```bash
./scripts/demo.sh escalate
```
Slow checkouts, no runbook, nobody answers. After the ack timeout the page goes to **bob**. The postmortem notes it and asks for a runbook.

**4. Silence (1.5 min).**
```bash
./scripts/demo.sh silence
```
A maintenance window: the alert fires but **nobody is paged**. Remove the silence and the page goes out.

**5. Inhibition (1.5 min).**
```bash
./scripts/demo.sh inhibit
```
Errors and slowness, one cause: `HighLatency` is **inhibited**, so there is one incident, not two.

**6. Close (30 s).** "Alert on symptoms, link a runbook, escalate, keep a timeline. Auto-fixes hide root causes: the postmortem says so. Paging here is simulated: no SMS or phone." All at once: `./scripts/demo.sh gameday`. Then `./scripts/destroy.sh` and terminate the instance.
