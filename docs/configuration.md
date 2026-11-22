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
| `IMAGE_CHECKPOINT` | empty (SDXL base 1.0) | another SDXL checkpoint, put in the `image-models` volume under `checkpoints/`; check its license |
| `VISION_MODEL` | `qwen3-vl:8b-instruct` | the Ollama vision model that reads handwriting (use an `-instruct` one) |
| `XTTS_ACCEPT_CPML` | empty | `yes` accepts the Coqui Public Model License of XTTS-v2 (**non-commercial use only**): without it the voice stays off |
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
| `image` | images: generate, upscale, cut out (ComfyUI) | ~10 GB, PyTorch shared | ~8 GB while working |
| `handwriting` | handwritten notes → text (qwen3-vl on Ollama) | ~0.15 GB (the model is in Ollama) | ~7 GB while reading |
| `voice` | text → speech (XTTS-v2, **non-commercial only**) | ~7 GB, PyTorch shared | ~3 GB |

```bash
docker compose --profile docs up -d          # or COMPOSE_PROFILES=docs,transcribe,speakers in .env
```

A service whose profile is off shows as *off* in **Services** and answers `503`. The GPU queue runs one
heavy job at a time; before each one, if VRAM is short, the gateway asks Ollama to free its models and the
other workers to unload. A worker that unloads restarts its container: it is the only way to give back all
of its memory, CUDA context included, and it is back in a few seconds.

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

## Build, start and stop from the web page

With the `control` profile, the **Services** tab gets **Build**, **Start** and **Stop** buttons for the six
optional services, so you build only what you use and turn things on when you need them:

```bash
docker compose --profile control up -d        # run it from the project folder
```

In family mode, put `COMPOSE_FILE=docker-compose.yml:docker-compose.local-ai.yml` in `.env`, so the buttons
use the same files as your stack. Without the profile, a service that is off shows the command to type.

Keeping every service on costs little: an idle service holds **no VRAM** (its model loads on the first job
and is freed after `IDLE_UNLOAD_MINUTES`) and some tens of MB of RAM. What weighs is the disk of the images
(up to ~35 GB) and their first build: that is what the buttons save.

**How it stays safe.** Something has to talk to Docker to build and start containers, and whoever controls
Docker controls the machine. So that power is kept in one small, closed container:

| Rule | So that |
|---|---|
| it knows six service names and three actions; anything else is refused before it runs | no free command can be sent |
| every command is a fixed list of words, run without a shell | nothing can be injected |
| `--no-deps`: it never starts, stops or rebuilds the gateway, Ollama, search, or itself | the page cannot take the core down |
| the project folder is mounted read-only | what gets built is what is in the repository |
| no published port, only the compose network; no Linux capabilities; read-only file system | it can be reached only by the gateway |
| the gateway allows the buttons only on `127.0.0.1` (the default) or with `LOCAL_AI_API_KEY` | a gateway opened to the network without a key refuses them |
| one action at a time, each one logged (`docker compose logs control`) | you can see what was done |

Even in the worst case, someone who can use the page can only build, start or stop local-ai-core's own
optional services.

## Security defaults

- listens on `127.0.0.1` unless told otherwise;
- optional API key, compared in constant time;
- request bodies capped at `MAX_UPLOAD_MB`;
- your notes are mounted read-only;
- containers run as an unprivileged user; workers have no published port;
- no telemetry; the only outgoing traffic is the model download from the Ollama registry;
- only the optional `control` service talks to Docker, closed as described above.
