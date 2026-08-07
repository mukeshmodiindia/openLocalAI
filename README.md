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

```bash
python3 scripts/generate_compose.py
# -> generated/podman-compose.<node-name>.yml   (one per node)
# -> generated/nginx-lb.conf + podman-compose.lb.yml   (only if a role has >1 node)
```

Then deploy — either manually per node (log into each and run
`podman-compose -f generated/podman-compose.<name>.yml up -d`), or all at
once from your local machine over SSH:

```bash
scripts/deploy_remote.sh
```

Pull models once on whichever node(s) run the `llm` role:
```bash
podman exec -it ollama ollama pull qwen2.5:14b
podman exec -it ollama ollama pull granite3-dense:8b
podman exec -it ollama ollama pull nomic-embed-text
```

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

## Status

This is a working scaffold: connectors, config loading, and the agent graph
are implemented and runnable, but you should treat prompts, tool routing,
and the Confluence/SN field mappings as a starting point to tune against
your actual instance schemas.
