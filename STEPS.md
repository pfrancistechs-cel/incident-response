# STEPS: Incident Response and On-Call (Alertmanager, runbook, postmortem)

## Prerequisites (set these up BEFORE step 1)

| Need | Details |
|---|---|
| **AWS account** | Any account that can launch EC2. No IAM role, no other AWS service. One region for everything. |
| **Instance type** | `m7i-flex.large` (2 vCPU, 8 GiB). The whole stack needs well under 1 GiB. |
| **OS image (AMI)** | Ubuntu Server 24.04 LTS (64-bit x86). 22.04 also works. |
| **Disk** | 20 GiB gp3. |
| **Key pair** | An RSA `.pem` key pair created in EC2 (step 0a). |
| **Security group** | Created in step 0b. **Inbound: exactly three rules, all with source My IP: SSH TCP 22; Custom TCP 8080 (the shop); Custom TCP 8081 (the on-call console).** **Outbound: leave the default (All traffic, 0.0.0.0/0)**, because the instance pulls three images from Docker Hub and apt needs the internet. Prometheus (9090) and Alertmanager (9093) are bound to localhost and need no rule. |
| **Code on the instance** | Option A: private GitHub repo plus a fine-grained token (Contents: Read) as the `git clone` password. Option B: copy the zip with `scp` (step 2). |
| **Accounts / tokens** | None. |
| **On your laptop** | A terminal with `ssh`/`scp` and a web browser. **No SSH tunnel is needed:** open `http://<PUBLIC-IP>:8081/` directly. |

**Time:** about 10 minutes of setup the first time (installs, one image pull, one full run of every scenario in preflight), about 8 minutes for the tour. **Cost:** about $0.10/hour at your quoted rate, plus pennies for the disk. Do step 11 when finished.

---

## Step 0: Create the key pair, security group and instance (AWS console)

**0a. Key pair.** EC2 > Network & Security > Key pairs > **Create key pair**. Name `ir-lab-key`, type **RSA**, format **.pem**. The file downloads once; keep it safe. Lock down its permissions:

```bash
# Linux / macOS
chmod 400 ~/Downloads/ir-lab-key.pem
```
```powershell
# Windows PowerShell (OpenSSH)
icacls "$HOME\Downloads\ir-lab-key.pem" /inheritance:r /grant:r "$($env:USERNAME):(R)"
```

**0b. Security group.** EC2 > Network & Security > Security Groups > **Create security group**.
- Name: `ir-lab-sg`, Description: `SSH and demo pages from my IP`, VPC: the default VPC.
- **Inbound rules > Add rule** (three times; Type `SSH` first, then `Custom TCP` twice; Protocol `TCP`; **Source `My IP`** every time):
  1. Port range `22` (SSH)
  2. Port range `8080` (the shop)
  3. Port range `8081` (the on-call console)
- **Outbound rules:** leave the default rule (`All traffic`, `0.0.0.0/0`).
- Click **Create security group**.

> Showing someone on another network? "My IP" blocks them. Share your screen, or add their public IP followed by `/32` to the 8080 and 8081 rules. Never use `0.0.0.0/0`.

**0c. Launch the instance.** EC2 > Instances > **Launch instances**.
- Name: `ir-lab-box`
- OS Image: **Ubuntu Server 24.04 LTS**, **64-bit (x86)**
- Instance type: `m7i-flex.large`
- Key pair: `ir-lab-key`
- Network settings > **Edit**: Auto-assign public IP **Enable**; **Select existing security group** > `ir-lab-sg`
- Storage: **20 GiB**, `gp3`
- Advanced details > IAM instance profile: **No IAM role**
- **Launch instance**, wait for `Running` and `2/2 passed`, copy the **Public IPv4 address** (`<PUBLIC-IP>` below).

> If your network changes, SSH and the pages time out: edit **all three** inbound rules and set Source to My IP again. Stop/start also changes the public IP.

## Step 1: Connect (from your laptop)

```bash
ssh -i ~/Downloads/ir-lab-key.pem ubuntu@<PUBLIC-IP>
```
Type `yes` at the fingerprint prompt. Everything below runs **on the instance**.

## Step 2: Update packages and get the code

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git unzip
```

**Option A (private GitHub repo):**
```bash
git clone https://github.com/<you>/incident-response.git
cd incident-response
```
Username = your GitHub username, password = the fine-grained token.

**Option B (no GitHub). From your laptop, in the folder holding the zip:**
```bash
scp -i ~/Downloads/ir-lab-key.pem incident-response.zip ubuntu@<PUBLIC-IP>:~
```
Then on the instance:
```bash
unzip incident-response.zip
cd incident-response
```

## Step 3: Make the scripts executable (do not skip)

Zipped or uploaded files often lose the executable flag, which gives `Permission denied`.

```bash
chmod +x scripts/*.sh scripts/*.py
ls -l scripts/        # every .sh and .py line should start with -rwxr-xr-x
```

## Step 4: Install Docker

```bash
./scripts/setup.sh
```
Runs `apt update`/`upgrade`, installs base tools (including `python3-yaml`), Docker Engine and Compose v2 from Docker's repo (fallback: Ubuntu's `docker.io`) and prints the versions. Takes 2-4 minutes and ends with `Setup complete.` If it says to log out and in for the docker group, either do that or keep going (the scripts use `sudo` by themselves).

## Step 5: Preflight (run well before the demo, and do not skip it)

```bash
./scripts/preflight.sh
```
Checks Docker, disk (4 GB), memory (1.5 GB), ports 8080, 8081, 9090 and 9093, and that Docker Hub is reachable; pulls `python:3.12-slim`, `prom/prometheus:v2.54.1` and `prom/alertmanager:v0.27.0`; **starts the stack**, runs `smoke.sh`, then runs **every demo scenario once** (`demo.sh gameday`, about 6 minutes) and the unit tests. Every line says `ok` and the last line is `Preflight passed. The whole stack works on this instance.` The stack is left running.

## Step 6: Start the stack

```bash
./scripts/up.sh
```
Starts the shop, traffic, Prometheus, Alertmanager and the on-call service (nothing is built) and prints:

```
On-call console : http://<PUBLIC-IP>:8081/
The shop        : http://<PUBLIC-IP>:8080/checkout
```

## Step 7: Health check

```bash
./scripts/smoke.sh
```
Every line says `ok` and it ends with **`All 12 checks passed.`**

## Step 8: Open the console (on your laptop)

`http://<PUBLIC-IP>:8081/`: the header shows who is on call (primary alice, secondary bob), the ack timeout and whether automatic runbooks are on. Panels: **Incidents** (status, assignee, time to acknowledge and resolve, an **Acknowledge** button, a postmortem link), **Timeline** of the newest incident, **Pager feed** (who was paged) and **Alertmanager** (active, silenced, inhibited). **Keep this tab open.** The shop answers at `http://<PUBLIC-IP>:8080/checkout`.

## Step 9: Run the tour (on the instance)

```bash
./scripts/demo.sh gameday          # all four scenarios: about 6 minutes
```
Or one at a time:
```bash
./scripts/demo.sh page             # alert, page, ack, automatic runbook, resolve, postmortem draft (about 1.5 minutes)
./scripts/demo.sh escalate         # no runbook, nobody answers: escalates to the secondary (about 2 minutes)
./scripts/demo.sh silence          # a maintenance window: fires but nobody is paged (about 1.5 minutes)
./scripts/demo.sh inhibit          # two alerts, one cause: one incident (about 1.5 minutes)
```
Each ends with a green `PASS`. By hand (or click in the console):
```bash
./scripts/ir.py status                  # incidents, schedule, Alertmanager alerts
./scripts/ir.py fault pool              # break the shop (pool | slow | both | ok to heal)
./scripts/ir.py ack latest alice        # acknowledge the newest incident
./scripts/ir.py automation off          # turn automatic runbooks off (on to turn them back on)
./scripts/ir.py postmortem latest       # print the postmortem draft
```
Postmortem drafts are also saved in `state/postmortems/INC-0001.md` and so on.

## Step 10: Unit tests (optional)

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```
Ends with `OK`. On the instance the app, on-call and repo tests run (about 30 seconds); the tests that need the `prometheus`, `alertmanager`, `promtool` and `amtool` binaries are skipped (this guide does not install them).

## Step 11: Clean up, then terminate the instance

```bash
./scripts/destroy.sh
```
Removes the containers, the network, the incidents, the postmortems and `.env`. In the AWS console: EC2 > Instances > `ir-lab-box` > **Instance state > Terminate instance**. Optionally delete `ir-lab-sg` and `ir-lab-key`.

---

## What you should see

| Step | Result |
|---|---|
| 4 | Docker and Compose versions, then `Setup complete.` |
| 5 | All `ok`, then `Preflight passed.` |
| 6 | The console and shop URLs with your public IP |
| 7 | `All 12 checks passed.` |
| 8 | No incidents, "nobody paged yet", no Alertmanager alerts |
| 9 | `page`: incident, alice paged, runbook ok, resolved in about 30 s, postmortem text, `PASS`. `escalate`: alice then bob paged, `PASS`. `silence`: silenced, no incident, then incident after unsilence, `PASS`. `inhibit`: HighLatency inhibited, one incident, `PASS`. Then `ALL SCENARIOS PASSED` |
| 10 | `OK` |

## Risky dependencies and fallbacks

| Dependency | Risk | Fallback |
|---|---|---|
| Docker Hub (`python:3.12-slim`, `prom/prometheus`, `prom/alertmanager`) | Rate limit on anonymous pulls | `sudo docker login` with a free account, then re-run preflight; images stay cached |
| Timing of alerts | The scenarios wait for 20-second rate windows and a 10-second `for`; a slow instance needs longer | Re-run the scenario; the waits are generous (up to 2 minutes) |
| Paging is simulated | No SMS or phone call is sent | Say so in the demo; in production use PagerDuty, Opsgenie or similar |
| Fault injection on port 8080 | `POST /_fault` is open to anyone who can reach the shop | The security group (My IP) is the only protection; demo only |
| Do not run `destroy.sh` before the demo | It removes the stack, incidents and `.env` | Re-run `./scripts/up.sh` afterwards |
| MinIO, LocalStack, S3 | **Not used** | none needed |

## If something goes wrong

| Symptom | Fix |
|---|---|
| SSH: `Connection timed out` | Your IP changed. Edit `ir-lab-sg`, inbound rule 22, Source My IP. Check the instance is Running with a public IP. |
| Console or shop never loads | (1) Use `http://` and ports 8081 (console) and 8080 (shop). (2) The rules need Source My IP and your current IP. (3) Check the instance's current public IP. (4) On the instance `curl -i http://localhost:8081/healthz` should return `200`; if so, it is the security group. |
| Testing the public IP from the instance hangs | Expected. Use `localhost` on the instance. |
| SSH: `Permission denied (publickey)` | User `ubuntu`, the right `.pem`, `chmod 400`. |
| `./scripts/x.sh: Permission denied` | `chmod +x scripts/*.sh scripts/*.py` (step 3), or `bash scripts/x.sh`. |
| `bad interpreter: /usr/bin/env bash^M` | `sed -i 's/\r$//' scripts/*.sh scripts/*.py` |
| `/usr/bin/env: 'python3\r'` | Same fix as the line above (Windows line endings). |
| Preflight: `Port NNNN is free` fails | `sudo ss -ltnp \| grep ':NNNN '`; stop that process, or `./scripts/destroy.sh` if it is an old run. |
| Preflight: a pull fails | Docker Hub rate limit: `sudo docker login`, re-run. |
| `up.sh`: a part does not answer | The message names the command: `sudo docker logs --tail 30 ir-oncall` (or `ir-prometheus`, `ir-alertmanager`, `ir-app`). |
| `oncall` logs `Permission denied` on `/state` | `chmod 777 state` (up.sh does it), then `sudo docker restart ir-oncall`. |
| Prometheus or Alertmanager container restarts | `sudo docker logs --tail 30 ir-prometheus` (or `ir-alertmanager`): a YAML mistake in `prometheus/` or `alertmanager/` is named there. |
| `smoke.sh`: `Prometheus is connected to Alertmanager` fails | Alertmanager needs a few seconds after start. Wait and re-run smoke; `sudo docker logs ir-alertmanager`. |
| `smoke.sh`: `the on-call service can reach Alertmanager` fails | `sudo docker ps` should show `ir-alertmanager` as `Up`; both containers must be on the `ir-net` network. |
| `demo.sh`: `the previous incident did not clear` | `./scripts/ir.py status` shows what is open; `./scripts/ir.py fault ok`, wait a minute, re-run. |
| `demo.sh page`: no incident appears | `curl -s localhost:9090/api/v1/alerts` shows whether Prometheus fired it; `curl -s localhost:9093/api/v2/alerts` whether Alertmanager got it; `sudo docker logs ir-oncall` whether the webhook arrived. |
| `demo.sh escalate`: no escalation | The ack timeout is 15 s (`ACK_TIMEOUT` in `.env`); do not acknowledge in the console during this scenario. |
| `demo.sh silence`: `no incident exists` fails | A previous silence or incident lingers: `./scripts/ir.py unsilence`, wait a minute, re-run. |
| `demo.sh`: `The stack is not running` | `./scripts/up.sh` |
| Disk full | `sudo docker system prune -af` (removes cached images; the next start pulls them again) |
