# Diagram prompt (paste into an AI image model)

Create a clean, wide infrastructure and flow diagram, white background, flat icons, thin labelled arrows, a legend at the bottom. Title: "Incident response and on-call on one EC2 instance".

Outer box: "EC2 instance m7i-flex.large (Docker, network ir-net)". Inside, a left-to-right flow of five containers:
1. "ir-app :8080 (shop)": a shopping-cart icon with a small lightning bolt "fault injection: pool exhausted / slow". "ir-traffic" (crowd icon, 20 req/s) sends requests into it.
2. "ir-prometheus (127.0.0.1:9090)": a flame icon, "scrapes /metrics every 5 s", with two bell icons "HighErrorRate: more than 5% errors for 10 s" and "HighLatency: average over 0.5 s for 10 s".
3. "ir-alertmanager (127.0.0.1:9093)": a funnel icon with four small labels "group", "silence", "inhibit (HighErrorRate mutes HighLatency)", "route". Arrow from Prometheus labelled "alerts".
4. "ir-oncall :8081": a phone/pager icon. Inside: "incident INC-0001", "on-call schedule: primary alice, secondary bob", "ack timeout, then escalate to bob", "runbook runner", "postmortem generator". Arrow from Alertmanager labelled "webhook: firing and resolved".
5. A wrench arrow from "runbook runner" back to the app labelled "POST /_admin/reset-pool, then verify 5 of 5 checkouts ok".

Output on the right: a browser window "on-call console" with an incidents table (status pills triggered / acknowledged / resolved), a timeline, a pager feed ("paged alice (primary)", "paged bob (secondary)") and an Alertmanager panel (active / silenced / inhibited), plus a document icon "postmortem INC-0001.md (timeline, metrics, action items)".

Outside the box: "Your laptop (My IP only)" with arrows to ":8081" and ":8080".

Security group note: "Inbound: 22, 8080, 8081 from My IP only. Prometheus 9090 and Alertmanager 9093 are localhost only". Small note: "Paging is simulated: no SMS or phone call (limit)".
