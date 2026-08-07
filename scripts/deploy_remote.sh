#!/usr/bin/env bash
# Drives a multi-node deploy from your LOCAL machine over SSH — you don't
# need to log into each node manually. Requires:
#   - passwordless (key-based) SSH to every host listed in deployment.nodes
#   - podman + podman-compose already installed on each remote host
#   - this repo already `git clone`d locally (this machine is just the
#     control point; it doesn't need Podman itself)
#
# What it does, per node:
#   1. rsyncs the repo (code + generated compose files + config.yaml + .env)
#      to the remote host
#   2. runs `podman-compose -f generated/podman-compose.<node>.yml up -d`
#      over SSH
#
# Usage:
#   python3 scripts/generate_compose.py     # regenerate compose files first
#   scripts/deploy_remote.sh                # deploy to every node in config.yaml
#   scripts/deploy_remote.sh node1-llm      # deploy to just one node
#
# SSH user/key: set REMOTE_USER / SSH_KEY env vars if not using your default.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

REMOTE_USER="${REMOTE_USER:-$(whoami)}"
SSH_OPTS="${SSH_KEY:+-i $SSH_KEY}"
REMOTE_PATH="${REMOTE_PATH:-~/openLocalAI}"

command -v python3 >/dev/null || { echo "python3 required"; exit 1; }
[ -f config.yaml ] || { echo "config.yaml not found — copy config.yaml.example first"; exit 1; }
[ -d generated ] || { echo "generated/ not found — run scripts/generate_compose.py first"; exit 1; }

ONLY_NODE="${1:-}"

NODE_LIST="$(python3 - "$ONLY_NODE" <<'PYEOF'
import sys, yaml
only = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else None
with open("config.yaml") as f:
    nodes = yaml.safe_load(f)["deployment"]["nodes"]
for n in nodes:
    if only and n["name"] != only:
        continue
    print(f"{n['name']}\t{n['host']}")
PYEOF
)"

if [ -z "$NODE_LIST" ]; then
  echo "No matching node found in config.yaml deployment.nodes"
  exit 1
fi

while IFS=$'\t' read -r NAME HOST; do
  COMPOSE_FILE="generated/podman-compose.${NAME}.yml"
  if [ ! -f "$COMPOSE_FILE" ]; then
    echo ">> Skipping $NAME — $COMPOSE_FILE not found (rerun generate_compose.py?)"
    continue
  fi

  echo ">> [$NAME @ $HOST] syncing repo..."
  rsync -az --delete \
    --exclude '.git' --exclude '__pycache__' --exclude 'data' \
    -e "ssh $SSH_OPTS" \
    ./ "${REMOTE_USER}@${HOST}:${REMOTE_PATH}/"

  echo ">> [$NAME @ $HOST] starting stack..."
  ssh $SSH_OPTS "${REMOTE_USER}@${HOST}" \
    "cd ${REMOTE_PATH} && podman-compose -f ${COMPOSE_FILE} up -d"

  echo ">> [$NAME @ $HOST] done."
done <<< "$NODE_LIST"

# If this node set includes an LLM node, remind to pull models (only needs
# doing once per llm node, not on every deploy).
if echo "$NODE_LIST" | grep -q "llm"; then
  echo
  echo ">> Reminder: pull models on your llm node(s) if you haven't yet:"
  echo "     ssh ${REMOTE_USER}@<llm-node-host> \"podman exec -it ollama ollama pull qwen2.5:14b\""
  echo "     ssh ${REMOTE_USER}@<llm-node-host> \"podman exec -it ollama ollama pull granite3-dense:8b\""
  echo "     ssh ${REMOTE_USER}@<llm-node-host> \"podman exec -it ollama ollama pull nomic-embed-text\""
fi

