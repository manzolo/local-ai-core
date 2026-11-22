# Configuration

[← back to the index](../README.md)

Everything is set with environment variables in a `.env` file next to `docker-compose.yml`
(`cp .env.example .env`). Each has a default: an empty `.env` is a working setup.

| Variable | Default | Meaning |
|---|---|---|
| `BIND_ADDR` | `127.0.0.1` | address the gateway listens on. `0.0.0.0` opens it to your network: set an API key too |
| `GATEWAY_PORT` | `8090` | port on the host |
| `LOCAL_AI_API_KEY` | empty | when set, every `/v1` call needs `X-API-Key: <key>` |
| `MAX_UPLOAD_MB` | `50` | largest request body accepted |
| `NOTES_DIR` | `./examples/notes` | the folder of Markdown notes to search, mounted read-only |
| `OLLAMA_HOST` | `http://ollama:11434` | where Ollama answers |
| `OLLAMA_MODEL` | `qwen3.5:9b` | the model that writes the answers |
| `EMBED_MODEL` | `bge-m3` | the model that turns text into vectors |
| `AUTO_PULL_MODELS` | `true` | download missing models at startup |
| `AUTO_INDEX` | `true` | index the notes at startup |
| `SEARCH_MIN_SCORE` | `0.35` | minimum similarity of a search hit (0–1) |
| `IDLE_UNLOAD_MINUTES` | `10` | Ollama and the GPU workers free a model's VRAM after this many minutes unused |
| `WHISPER_MODEL` | `large-v3` | the faster-whisper model (`small`, `medium`, `large-v3`…): smaller is faster and less accurate |

After a change: `docker compose up -d` (only the containers whose settings changed restart).

## Profiles

Light services start by default; heavy ones are compose profiles you turn on:

| Profile | Adds | Image | VRAM |
|---|---|---|---|
| (default) | gateway, search, ask, Ollama | ~0.3 GB + Ollama | ~8 GB while answering |
| `docs` | documents → Markdown (Docling) | ~7 GB | ~3 GB while converting |
| `transcribe` | speech → text and subtitles (faster-whisper) | ~3.4 GB | ~5 GB while transcribing |
| `speakers` | who said what (SpeechBrain) | ~6.6 GB, PyTorch shared with `docs` | ~1 GB |

```bash
docker compose --profile docs up -d          # or COMPOSE_PROFILES=docs,transcribe,speakers in .env
```

A service whose profile is off shows as *off* in **Services** and answers `503`. The GPU queue runs one
heavy job at a time; before a GPU worker starts, the gateway asks Ollama to free its models if VRAM is
short.

## Recipes

**Open to the LAN, with a key.**

```bash
BIND_ADDR=0.0.0.0
LOCAL_AI_API_KEY=<paste here the output of: openssl rand -hex 24>
```

**A smaller model for a smaller GPU.** Any chat model in the [Ollama library](https://ollama.com/library)
works; it is downloaded at the next start.

```bash
OLLAMA_MODEL=qwen3.5:4b
```

**Changing the embedding model** makes the index useless: it is rebuilt by itself on the next search
(the index remembers which model made it).

## Security defaults

- listens on `127.0.0.1` unless told otherwise;
- optional API key, compared in constant time;
- request bodies capped at `MAX_UPLOAD_MB`;
- your notes are mounted read-only;
- containers run as an unprivileged user; workers have no published port;
- no telemetry; the only outgoing traffic is the model download from the Ollama registry.
