# Local Open-Source AI Agent for Change-Task Planning
### Architecture, model choice, Podman packaging, and hardware sizing

---

## 1. What you're building

A locally-hosted agent, reachable via a chat-style UI, that:
- Takes a request like *"prepare a plan for a change task: upgrade MySQL replication topology"*
- Checks **ServiceNow** for similar past change tasks / templates (source of truth for repeatable tasks)
- Pulls procedural knowledge from **Confluence** (your internal runbooks)
- For things not in your internal docs (e.g. "current DB size" queries, user creation syntax) — queries **MySQL/MongoDB/PostgreSQL directly** or looks up **official vendor documentation** live
- Can post results / ask for approval via **Slack**
- Ships as a package with one `config.yaml` for all connection strings
- Runs under **Podman**, not Docker
- 100% open source, no token/API costs — only your own compute

---

## 2. Component stack (all open source)

| Layer | Tool | Why |
|---|---|---|
| LLM runtime | **Ollama** (simplest) or **llama.cpp** (more control) or **vLLM** (if you get a GPU, much higher throughput) | GGUF quantized models, no license fees |
| Agent orchestration | **LangGraph** (fine-grained control flow) or **CrewAI** (simpler multi-agent) | Both MIT-licensed, handle planning + tool calling |
| Vector DB / RAG | **Qdrant** or **Chroma** | Indexes Confluence pages + cached vendor docs for retrieval |
| Web search (for live MySQL/Mongo doc lookups) | **SearxNG** (self-hosted meta search) | Avoids paying for Google/Bing API; fully local |
| UI | **Open WebUI** (chat UI, supports custom tools/functions) or a small custom FastAPI+React front end | Open source, works with Ollama out of the box |
| ServiceNow connector | ServiceNow **REST Table API** (`/api/now/table/change_request` etc.) via Python `requests` | No SDK licensing, just REST + basic/OAuth auth |
| Confluence connector | Confluence **REST API** (Cloud or Data Center) via `atlassian-python-api` (open source lib) | Pulls page content, spaces, attachments |
| DB task drivers | `mysql-connector-python`, `pymongo`, `psycopg2` | Direct queries for things like DB size, user list |
| Slack integration | **Slack Bolt SDK** (Python), socket mode (no public webhook needed) | Free, works behind your firewall |
| Packaging | **Podman + podman-compose** (or Quadlet `.container` unit files for systemd-managed rootless containers) | Daemonless, rootless — matches your "no Docker" requirement |

---

## 3. LLM model choice

Given no GPU is mentioned in your spec — **this is the constraint that matters most.** CPU-only inference is workable for a background "plan generation" agent (not snappy chat), so plan accordingly.

**Primary recommendation:** `Qwen 3.6 14B-Instruct`, GGUF, `Q4_K_M` quantization
- Best tool-calling reliability among open models that fit in 32GB RAM
- Apache 2.0 license (fully permissive, no restrictions)
- ~9GB on disk quantized, ~10-12GB RAM at runtime with context

**Alternative / fallback:** `IBM Granite 3.x 8B-Instruct`
- Purpose-built for enterprise RAG and tool-use tasks
- Meaningfully faster on CPU (smaller model)
- Worth A/B testing against Qwen specifically on your SN/Confluence retrieval tasks — smaller, RAG-tuned models sometimes outperform larger general models on narrow lookup tasks

**If you ever add a GPU:** re-evaluate with `GLM-5.2` or `DeepSeek V4` — both currently benchmark ahead of Qwen on agentic tasks but need real GPU VRAM (24GB+) to be practical.

**Don't do:** don't reach for 70B+ models on this hardware. Even quantized, they'll be too slow for practical use without a GPU.

---

## 4. High-level build steps

1. **Install Podman + podman-compose**
   ```bash
   sudo apt install podman podman-compose   # or dnf on RHEL/Fedora
   ```
2. **Stand up Ollama in a rootless Podman container**, pull `qwen2.5:14b` (or the latest Qwen 3.6 tag once available in the Ollama library) and `granite3-dense:8b`.
3. **Stand up Qdrant** as a container for the vector index.
4. **Stand up SearxNG** as a container for live web lookups (used only for external vendor docs like MySQL/MongoDB manuals — keep this scoped, don't let the agent browse arbitrarily).
5. **Write the connectors** (Python) for ServiceNow, Confluence, MySQL/Mongo/PG, Slack — each reads its connection details from one `config.yaml`.
6. **Build the agent graph** in LangGraph:
   - `planner` node: decides whether the task is "repeatable" (→ query SN) or "needs live info" (→ query DB / web / Confluence)
   - `retriever` node: RAG against Qdrant (Confluence + cached vendor docs)
   - `tool-executor` node: runs DB read-only queries, SN API calls
   - `writer` node: assembles the change-task plan document
   - `notifier` node: posts to Slack for review
7. **Wrap it all with Open WebUI** as the chat front end, pointed at your Ollama endpoint, with the LangGraph agent exposed as a custom "pipeline"/function so tool calls route through it.
8. **Package as `podman-compose.yml`** + one `config.yaml.example` that ships with the repo; user only edits connection strings and secrets.

### Example `config.yaml` shape
```yaml
llm:
  provider: ollama
  model: qwen2.5:14b
  fallback_model: granite3-dense:8b

servicenow:
  instance_url: https://yourinstance.service-now.com
  username: ${SN_USER}
  password: ${SN_PASS}

confluence:
  base_url: https://yourcompany.atlassian.net/wiki
  username: ${CONF_USER}
  api_token: ${CONF_TOKEN}

databases:
  mysql:
    host: db1.internal
    user: ${MYSQL_USER}
    password: ${MYSQL_PASS}
  mongodb:
    uri: mongodb://${MONGO_USER}:${MONGO_PASS}@mongo1.internal:27017
  postgres:
    host: pg1.internal
    user: ${PG_USER}
    password: ${PG_PASS}

slack:
  bot_token: ${SLACK_BOT_TOKEN}
  app_token: ${SLACK_APP_TOKEN}
  channel: "#change-tasks"
```

### Podman notes vs Docker
- Use `podman-compose up -d` — mostly drop-in compatible with a `docker-compose.yml`.
- For production-grade, prefer **Quadlet** (`.container` unit files run by systemd) over `podman-compose` — better restart/health behavior, no compose daemon needed.
- Rootless by default — run each container as your own user, no root daemon like dockerd.
- Use a Podman **pod** (`podman pod create`) to group Ollama + Qdrant + your agent service so they share a network namespace like a Docker Compose project would.

---

## 5. Hardware sizing — single machine (32GB RAM / 32 CPU / 200GB disk)

**What fits:**
- Qwen 14B Q4 (~9GB) + Granite 8B Q4 (~5GB) loaded on demand — don't run both simultaneously, load whichever's needed (Ollama does this automatically, unloading idle models)
- Qdrant with a few hundred thousand vectors — trivial on this hardware
- SearxNG, connectors, orchestrator — lightweight, a few hundred MB RAM combined

**Disk budget (200GB is generous but check this math):**
- Model weights: 10–30GB depending on how many quantized models you keep
- Container images: 5–10GB
- Vector DB + Confluence page cache: a few GB unless you're indexing a huge Confluence instance
- Logs/conversation history: negligible unless you keep years of history

**What's missing / what to add:**
- **No GPU is the main gap.** Even a single mid-range GPU (16GB VRAM, e.g. RTX 4060 Ti / 4070) would let you run the 14B model at 40-80 tok/s instead of 3-8 tok/s, and opens the door to 27-32B models.
- **Swap space**: add 16-32GB swap as a safety net — quantized 14B models plus a large context window plus Qdrant can spike memory.
- **Fast disk (NVMe SSD)** — matters a lot for model load time; if your 200GB is spinning disk, model loads will be slow every time Ollama swaps models.
- Everything else (32 CPU cores) is more than sufficient — LLM inference on CPU is memory-bandwidth-bound, not core-count-bound, so 32 cores is actually more than you'll use; 8-16 fast cores would perform almost identically.

---

## 6. Scaling to 3+ machines (32GB/32CPU/200GB each)

Two very different strategies — pick based on your actual bottleneck.

### Option A — Split by service (recommended for your use case)
Don't try to make the LLM itself distributed. Instead, dedicate each machine to a layer:
- **Machine 1 — LLM serving**: runs Ollama/vLLM exclusively, all 32GB dedicated to model weights + context. Lets you comfortably run a 32B model or run 14B with a much larger context window / higher concurrency.
- **Machine 2 — Data & RAG**: Qdrant, SearxNG, Confluence/SN indexing jobs, DB connectors. Keeps I/O-heavy retrieval work off the inference box.
- **Machine 3 — Orchestration & UI**: LangGraph agent runtime, Open WebUI, Slack bot, and the internal SQLite/Postgres history store. Handles concurrency (multiple users/requests) without competing with the LLM for RAM.

This is simple, robust, and gives you real horizontal scaling — e.g. Machine 3 can be replicated further if many people use the agent concurrently, while Machine 1 stays a single dedicated inference box.

**This is implemented, not just described.** `config.yaml`'s `deployment.nodes`
list is exactly this 3-node layout by default. `scripts/generate_compose.py`
reads it and emits one compose file per node, plus (once any role has more
than one node) an nginx config that load-balances across them — weighted by
each node's declared `resources.ram_gb`, recomputed automatically every time
you rerun the script. Concretely:

```yaml
deployment:
  nodes:
    - name: node1-llm
      host: 10.0.0.11
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

Adding a 4th machine — say a bigger box to run a second LLM replica — is
just adding an entry:

```yaml
    - name: node4-llm-big
      host: 10.0.0.14
      roles: [llm]
      resources: { cpu: 64, ram_gb: 128 }
```

Rerunning `python3 scripts/generate_compose.py` at that point automatically
starts generating `generated/nginx-lb.conf` with weighted upstreams — the
128GB node gets roughly 4x the traffic share of the 32GB node — and a
`generated/podman-compose.lb.yml` to run nginx. No hand-written load
balancer config, no code changes; only the compose files for the
newly-affected nodes need to be redeployed.

### Option B — Distributed inference (only if you need bigger models than one box can hold)
`llama.cpp` supports an **RPC backend** that splits a single model's layers across multiple machines, and `vLLM`/`Ray` support tensor/pipeline parallelism across nodes. This lets 3 machines pool RAM to run something like a 70B model.

**Caveats:**
- Needs low-latency, high-bandwidth networking between nodes (10GbE minimum; regular gigabit LAN will bottleneck badly) — CPU-based distributed inference over slow interconnects can end up *slower* than a single box running a smaller model.
- Meaningfully more complex to operate and debug.
- Given your workload (occasional change-task planning, not high-volume production inference), **Option A will almost certainly serve you better** than distributed inference. Reach for Option B only if a single-box model quality genuinely isn't good enough and adding a GPU isn't possible.

### Practical recommendation
Start on one machine to validate the whole pipeline works end-to-end (connectors, RAG quality, plan quality). Once it's proven useful, split into Option A's 3-machine layout for headroom and resilience — that gets you the most value from the extra hardware without the complexity of true distributed inference.

---

## 7. Suggested build order (checklist)

1. Podman + Ollama running, Qwen 14B pulled, basic chat working
2. Add Qdrant, index a handful of Confluence pages, confirm RAG retrieval works
3. Add ServiceNow connector, pull change_request records, feed into planning prompt
4. Add MySQL/MongoDB/PG read-only connectors for direct data checks
5. Add SearxNG for live vendor-doc lookups (scope it to specific domains: `dev.mysql.com`, `mongodb.com/docs`)
6. Wire it all into a LangGraph agent with the planner → retriever → tool-executor → writer flow
7. Add Slack notification/approval step
8. Package as `podman-compose.yml` + `config.yaml.example`, write a README for setup
9. A/B test Qwen 14B vs Granite 8B on real change-task prompts, pick a default
