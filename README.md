# openLocalAI

A locally-hosted, fully open-source AI agent for infra/ops teams. It plans
change tasks using **ServiceNow** as the source of truth for repeatable work,
pulls procedural knowledge from **Confluence**, answers ad-hoc database
questions (MySQL / MongoDB / PostgreSQL) directly, looks up official vendor
docs live when needed, and can notify/collaborate via **Slack**.

No token costs, no API licensing — everything runs on your own hardware
under Podman.

## Stack

| Layer | Tool |
|---|---|
| LLM runtime | [Ollama](https://ollama.com) (Qwen 3.6 14B default, Granite 3.x 8B fallback) |
| Agent orchestration | [LangGraph](https://github.com/langchain-ai/langgraph) |
| Vector DB / RAG | [Qdrant](https://qdrant.tech) |
| Live vendor-doc lookup | [SearxNG](https://github.com/searxng/searxng) (self-hosted, scoped to allow-listed domains) |
| UI | [Open WebUI](https://github.com/open-webui/open-webui) |
| Containers | Podman + podman-compose |

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design writeup
(model choice rationale, hardware sizing, single-node vs multi-node layout).

## Prerequisites (run on every node)

These steps are needed on **every machine that will run a container** —
for single-node that's just the one box; for multi-node, run this on all
3+ servers before deploying anything.

```bash
# 1. Core packages
sudo dnf install git -y
sudo dnf install python3-pip -y
sudo dnf install podman -y
pip install podman-compose

# 2. Enable a systemd/D-Bus user session (required for rootless Podman —
#    without this, builds fail with "sd-bus call: Interactive
#    authentication required")
sudo loginctl enable-linger $(whoami)
exit   # log out from the server, then connect again (env vars are only
       # set at login time — re-running commands in the same shell won't
       # pick up the new session)
```

Reconnect over SSH, then verify the session actually picked up:

```bash
echo $XDG_RUNTIME_DIR             # expect /run/user/<your-uid>
echo $DBUS_SESSION_BUS_ADDRESS    # expect unix:path=/run/user/<your-uid>/bus
loginctl show-user $(whoami) | grep Linger   # expect Linger=yes
```

On Rocky Linux / RHEL 9, also install the `ip_tables` kernel module —
minimal installs often don't ship it, and container networking (netavark)
needs it:

```bash
sudo dnf install -y kernel-modules-extra
sudo modprobe ip_tables ip6_tables iptable_nat ip6table_nat
```

**Recommended, especially on 32GB-RAM nodes:** add swap as a safety net.
A 14B model plus a large context window plus Qdrant can spike memory, and
without swap the OOM killer can silently kill containers under load.

```bash
df -h /                                     # confirm free disk space first
sudo fallocate -l 32G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

## Quick start

## Where do you run this from?

**Your local machine, driving the 3 (or more) remote nodes over SSH** — you
don't log into each node by hand:

```bash
git clone https://github.com/mukeshmodiindia/openLocalAI.git
cd openLocalAI
cp config.yaml.example config.yaml
cp .env.example .env
# edit config.yaml: fill in deployment.nodes with your 3 real hosts

python3 scripts/generate_compose.py       # builds per-node compose files
scripts/deploy_remote.sh                  # rsyncs + starts each node over SSH
```

Requirements: passwordless SSH (key-based) from your machine to each node,
and Podman + podman-compose already installed on each remote host — this
script doesn't install Podman for you, only deploys the stack onto it. Your
local machine itself doesn't need Podman; it's just the control point.

Deploying a single node again later (e.g. after adding a 4th machine) is
`scripts/deploy_remote.sh <node-name>`.

## Deploy first, add ServiceNow/Confluence/DB connections later?

Yes — this is the intended flow, not an afterthought. In
`config.yaml.example`, every integration (ServiceNow, Confluence, MySQL,
MongoDB, PostgreSQL, Slack) defaults to `enabled: false`. Deploy with just
that, and the agent runs fine with only the LLM + UI — the corresponding
tools simply don't exist yet, so the model never tries to use them.

When you're ready to add one:

```bash
# edit config.yaml — e.g. flip servicenow.enabled: true and fill in instance_url/auth
curl -X POST http://<orchestration-node>:8000/admin/reload-config
```

No redeploy, no container restart — the agent re-reads `config.yaml` and the
new tool becomes available immediately. Check what's currently wired up
with `GET /integrations`.

**For a genuinely one-off DB task** (you don't want to add a database to
`config.yaml` at all — just run one query against it), skip config entirely:

```bash
curl -X POST http://<orchestration-node>:8000/db/adhoc-query \
  -H 'Content-Type: application/json' \
  -d '{
    "provider": "mysql",
    "connection": {"host": "db3.internal", "user": "readonly_user", "password": "...", "port": 3306},
    "operation": "database_size",
    "params": {"schema": "orders"}
  }'
```

Same pattern for `mongodb` (`connection: {"uri": "mongodb://..."}`) and
`postgres`.

### Option A — single node (test/dev, or one big box)

```bash
git clone https://github.com/mukeshmodiindia/openLocalAI.git
cd openLocalAI
cp config.yaml.example config.yaml
cp .env.example .env
# edit config.yaml:
#   - deployment.mode: single_node
#   - llm.host: http://ollama:11434            (leave as-is, container DNS works here)
#   - internal_store.postgres.host: store-postgres   (container name, not an IP, for single-node)
# edit .env with your SN/Confluence/DB/Slack/STORE_PG credentials

podman-compose -f podman-compose.single-node.yml up -d
podman exec -it ollama ollama pull qwen2.5:14b
podman exec -it ollama ollama pull granite3-dense:8b
podman exec -it ollama ollama pull nomic-embed-text
# UI at http://localhost:8080
```

### Option B — multiple nodes, auto load-balanced

`config.yaml`'s `deployment.nodes` list describes every machine and what runs
on it. `scripts/generate_compose.py` turns that list into one compose file
per node, plus an nginx config that load-balances across any role with more
than one node — weighted by each node's declared RAM, so a bigger box
automatically gets more traffic. No manual LB config, ever — just edit the
node list and regenerate.

**Example: 3 nodes, 32 CPU / 32GB RAM each** (the recommended split — one
role per box):

```yaml
deployment:
  mode: multi_node
  nodes:
    - name: node1-llm
      host: 10.0.0.11          # replace with your real node IPs
      roles: [llm]
      resources: { cpu: 32, ram_gb: 32 }
    - name: node2-data
      host: 10.0.0.12
      roles: [data]
      resources: { cpu: 32, ram_gb: 32 }
    - name: node3-orchestration
      host: 10.0.0.13
      roles: [orchestration]
      resources: { cpu: 32, ram_gb: 32 }
```

Also update `llm.host` in `config.yaml` to the real LLM node's IP —
container DNS names like `http://ollama:11434` only resolve within a single
host's Podman bridge network, not across physical machines:

```yaml
llm:
  host: http://10.0.0.11:11434
internal_store:
  postgres:
    host: 10.0.0.12            # the data-role node's real IP, not a container name
```

#### Step 1 — run the Prerequisites section above on all 3 nodes

Repeat the entire **Prerequisites (run on every node)** section above on
`node1-llm`, `node2-data`, and `node3-orchestration` individually before
continuing. Each is a separate machine with its own Podman install, D-Bus
session, kernel modules, and (recommended) swap.

#### Step 2 — generate the per-node compose files (from your local machine or any one node)

```bash
git clone https://github.com/mukeshmodiindia/openLocalAI.git
cd openLocalAI
cp config.yaml.example config.yaml   # fill in deployment.nodes as above
cp .env.example .env                 # fill in credentials

python3 scripts/generate_compose.py
# -> generated/podman-compose.node1-llm.yml
# -> generated/podman-compose.node2-data.yml
# -> generated/podman-compose.node3-orchestration.yml
# -> generated/nginx-lb.conf + podman-compose.lb.yml   (only if a role has >1 node)
```

#### Step 3 — deploy each node

**Option 3a — manually, one command per node** (clearest for a first deploy
or for troubleshooting — run these on each respective machine):

```bash
# --- on node1-llm (10.0.0.11) ---
cd ~/openLocalAI
podman-compose -f generated/podman-compose.node1-llm.yml up -d
podman exec -it ollama ollama pull qwen2.5:14b
podman exec -it ollama ollama pull granite3-dense:8b
podman exec -it ollama ollama pull nomic-embed-text
```

```bash
# --- on node2-data (10.0.0.12) ---
cd ~/openLocalAI
podman-compose -f generated/podman-compose.node2-data.yml up -d
```

```bash
# --- on node3-orchestration (10.0.0.13) ---
cd ~/openLocalAI
podman-compose -f generated/podman-compose.node3-orchestration.yml up -d
```

You'll need the repo (with your filled-in `config.yaml` and `.env`) present
on each node for this — either `git clone` + copy your edited config files
to each host, or `scp` the whole directory over.

**Option 3b — all at once from your local machine over SSH** (once
passwordless/key-based SSH is set up from your machine to all 3 nodes, and
Podman is already installed on each per Step 1):

```bash
scripts/deploy_remote.sh
```

This rsyncs the repo + your `config.yaml`/`.env` to each node and starts the
right compose file on each automatically. Deploying a single node again
later (e.g. after changing just one node's config) is
`scripts/deploy_remote.sh <node-name>`.

Pull models once on whichever node(s) run the `llm` role (only needed once,
even with `deploy_remote.sh`):
```bash
podman exec -it ollama ollama pull qwen2.5:14b
podman exec -it ollama ollama pull granite3-dense:8b
podman exec -it ollama ollama pull nomic-embed-text
```

#### Step 4 — verify

On each node:
```bash
podman ps -a
podman-compose -f generated/podman-compose.<node-name>.yml logs -f
```

UI (served from the orchestration node) at `http://10.0.0.13:8080` (adjust
to your real orchestration node IP/port).

**Scaling out:** add a node (or a bigger replacement node) under
`deployment.nodes`, rerun `generate_compose.py`, redeploy the changed
compose files on the affected hosts — the nginx weights recompute
automatically from each node's declared `resources.ram_gb`. See
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the reference 3-node
layout (LLM / data / orchestration) and sizing guidance.


## What database does this use?

Two different, non-overlapping things:

1. **External data sources you connect to** — your existing MySQL, MongoDB,
   PostgreSQL (for ad-hoc queries like "current DB size"), ServiceNow, and
   Confluence. These are never written to except the explicit, opt-in
   `create_user`-style calls — see `src/connectors/`. All configured in
   `config.yaml`.
2. **The agent's own internal store** — every plan it generates, and any
   Slack approve/reject decision, gets logged here for audit/history.
   Defaults to **Postgres** (deployed for you as a `store-postgres`
   container on the `data` role — see `podman-compose.single-node.yml` /
   `scripts/generate_compose.py`), which is the right default once more than
   one `orchestration` node exists, since they all need to share one
   history. Set `internal_store.provider: sqlite` instead if you want a
   single-file, zero-extra-infra option for a single-node/single-replica
   setup. See `src/store.py`.

## Repo layout

```
openLocalAI/
├── config.yaml.example        # single config file for ALL integrations + topology
├── .env.example                 # secrets referenced from config.yaml
├── podman-compose.single-node.yml   # everything on one box
├── requirements.txt
├── scripts/
│   ├── setup.sh                  # single-node bootstrap
│   └── generate_compose.py       # reads deployment.nodes -> per-node compose
│                                  # files + auto-weighted nginx LB config
├── src/
│   ├── config.py                 # loads config.yaml + resolves ${ENV_VARS}
│   ├── main.py                   # FastAPI entrypoint for the agent service
│   ├── store.py                  # internal plan/audit history (SQLite/Postgres)
│   ├── llm/client.py              # Ollama client wrapper, model fallback logic
│   ├── connectors/
│   │   ├── servicenow.py         # SN REST Table API client
│   │   ├── confluence.py         # Confluence REST API client
│   │   ├── mysql_conn.py
│   │   ├── mongo_conn.py
│   │   ├── postgres_conn.py
│   │   └── slack_conn.py         # Slack Bolt SDK, socket mode
│   ├── rag/
│   │   ├── vector_store.py       # Qdrant wrapper for Confluence + doc cache
│   │   └── web_search.py         # SearxNG client, domain allow-list
│   └── agent/
│       ├── tools.py               # LangGraph tool definitions wrapping connectors
│       ├── planner.py             # prompts for plan generation
│       └── graph.py               # the LangGraph state graph itself
├── containers/
│   └── Containerfile.agent        # image for the agent service
├── generated/                      # git-ignored — output of generate_compose.py
└── docs/
    └── ARCHITECTURE.md
```

## Troubleshooting

Real errors hit during Rocky Linux 9 deployments, in the order they tend to
show up, with the exact fix:

| Error | Cause | Fix |
|---|---|---|
| `sd-bus call: Interactive authentication required.: Permission denied` during build | Rootless Podman's systemd cgroup manager has no live D-Bus user session — very common on a fresh box or right after SSH login before lingering is enabled | `sudo loginctl enable-linger $(whoami)`, then fully disconnect/reconnect SSH (env vars are only set at login time). Verify with `echo $XDG_RUNTIME_DIR`. Fallback: set `cgroup_manager = "cgroupfs"` in `~/.config/containers/containers.conf` |
| `no compose.yaml, docker-compose.yml or container-compose.yml file found` | podman-compose looks for default filenames, but this repo's files are named `podman-compose.single-node.yml` / `generated/podman-compose.<node>.yml` | Always pass `-f <filename>` explicitly |
| `netavark: ... could not insert 'ip_tables': Operation not permitted` when containers start | Rocky/RHEL 9 minimal installs often don't ship the `ip_tables` kernel module — it's in `kernel-modules-extra`, not installed by default | `sudo dnf install -y kernel-modules-extra` then `sudo modprobe ip_tables ip6_tables iptable_nat ip6table_nat` |
| `"nomic-embed-text:latest" does not support chat` | `nomic-embed-text` is embedding-only (used internally for RAG/vector search), accidentally selected as the active chat model in Open WebUI | Select `qwen2.5:14b` or `granite3-dense:8b` in the WebUI model dropdown. Never select `nomic-embed-text` for chat |
| Chat responses very slow | Expected on CPU-only hardware — a 14B model runs roughly 3-8 tok/s without a GPU; this is memory-bandwidth-bound, not fixed by adding more cores | Prefer the smaller `granite3-dense:8b` day-to-day; add swap as a safety net. A GPU (even a 16GB consumer card) gives roughly a 5-10x speedup — the single biggest lever available. See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for sizing guidance |
| Extremely slow + load average far above CPU count, swap filling up | Two models loaded and generating **simultaneously** (e.g. after switching models in the UI, Ollama kept the old one warm) — confirm with `podman exec -it ollama ollama ps`, and `top` will show two `llama-server` processes each pinning 100%+ CPU | `podman exec -it ollama ollama stop <model>` to unload the extra one immediately. Long-term fix (already applied in this repo's compose templates): `OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_NUM_PARALLEL=1`, `OLLAMA_KEEP_ALIVE=2m` on the `ollama` service |
| `Trying to pull docker.io/ghcr.io/open-webui/open-webui:main ... access denied` | A `docker.io/` prefix was incorrectly applied to a `ghcr.io` image, which isn't a Docker Hub image at all | Already fixed — `webui` image is `ghcr.io/open-webui/open-webui:main` |

## Status

This is a working scaffold: connectors, config loading, and the agent graph
are implemented and runnable, but you should treat prompts, tool routing,
and the Confluence/SN field mappings as a starting point to tune against
your actual instance schemas.
