# Join the local-ai-lab family

[← back to the index](../README.md)

local-ai-core belongs to [local-ai-lab](https://github.com/manzolo/local-ai-lab), a family of self-hosted
AI projects that share one Docker network and one Ollama. It follows the family's conventions:

| Convention | Here |
|---|---|
| **Standalone first** | `docker compose up -d` works alone, with its own Ollama, and never touches `local-ai-net` |
| **One shared network**, `local-ai-net` | joined only through the opt-in override `docker-compose.local-ai.yml` |
| **One way to find the LLM**, `OLLAMA_HOST` | default `http://ollama:11434` |

## Use the family's Ollama

When [ollama-docker](https://github.com/manzolo/ollama-docker) is already running, a second Ollama would
only take VRAM and disk twice. The override turns ours off and joins the shared network:

```bash
cd ../ollama-docker && docker compose up -d       # creates local-ai-net and the `ollama` container
cd ../local-ai-core
docker compose -f docker-compose.yml -f docker-compose.local-ai.yml up -d
```

(`make up-family` does the same.) On `local-ai-net`, the name `ollama` is the family's container, so
nothing else changes: search, ask and handwriting all use it. The models the stack needs are downloaded into that Ollama if it lacks them.

No `ollama-docker`, but you want the network for other projects: `docker network create local-ai-net`.

## Reach local-ai-core from the family

On `local-ai-net` the gateway answers at **`http://local-ai-core:8090`**, so another project can call it
without going through the host:

```bash
docker run --rm --network local-ai-net curlimages/curl -s http://local-ai-core:8090/v1/status
```

## Going back to standalone

```bash
docker compose -f docker-compose.yml -f docker-compose.local-ai.yml down
docker compose up -d
```

The standalone Ollama keeps its models in its own volume: the first standalone start downloads them.
