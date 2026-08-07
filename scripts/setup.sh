#!/usr/bin/env bash
# One-shot local bootstrap for openLocalAI.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

command -v podman >/dev/null 2>&1 || { echo "podman not found. Install it first (e.g. apt install podman podman-compose)."; exit 1; }
command -v podman-compose >/dev/null 2>&1 || { echo "podman-compose not found. Install it: pip install podman-compose"; exit 1; }

if [ ! -f config.yaml ]; then
  cp config.yaml.example config.yaml
  echo ">> Created config.yaml from template — edit it with your SN/Confluence/DB/Slack details before continuing."
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo ">> Created .env from template — fill in your secrets before continuing."
fi

read -rp "Press Enter once config.yaml and .env are filled in (or Ctrl+C to stop and edit now)..."

echo ">> Starting stack..."
podman-compose up -d

echo ">> Waiting for Ollama to be ready..."
until curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; do sleep 2; done

echo ">> Pulling models (this may take a while on first run)..."
podman exec -it ollama ollama pull qwen2.5:14b
podman exec -it ollama ollama pull granite3-dense:8b
podman exec -it ollama ollama pull nomic-embed-text

echo ">> Done. Open WebUI: http://localhost:8080  |  Agent API: http://localhost:8000/healthz"
