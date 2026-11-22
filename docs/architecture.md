# Architecture

[← back to the index](../README.md)

One public process, the **gateway**, in front of small **workers** that never leave the compose network,
and **Ollama** running the models on the GPU.

```mermaid
flowchart LR
    U["browser · curl · scripts"] -->|"HTTP 127.0.0.1:8090"| G
    subgraph stack["docker compose (local-ai-core)"]
        G["gateway<br/>/v1 API · OpenAPI · web UI<br/>jobs + GPU queue"]
        S["search worker<br/>chunks · index · checked citations"]
        D["documents worker (profile docs)<br/>Docling: layout · tables · OCR"]
        G -->|"/convert (file in the uploads volume)"| D
        TR["transcriber (profile transcribe)<br/>faster-whisper"]
        SP["speakers (profile speakers)<br/>SpeechBrain ECAPA"]
        G -->|"/transcribe"| TR
        G -->|"/attribute"| SP
        D -.->|"Markdown into the library"| L[("library")]
        L --> S
        O[("ollama<br/>bge-m3 · qwen3.5:9b")]
        G -->|"/search /ask /index /selftest"| S
        S -->|"embeddings, chat"| O
        G -->|"status, model download"| O
    end
    N[/"NOTES_DIR<br/>(read-only)"/] --> S
```

## The pieces

| Piece | Where | Role |
|---|---|---|
| gateway | `gateway/` | the only listening port. Serves `/v1/…`, `/openapi.json`, `/docs` and the web page. Runs long work as jobs and keeps one GPU queue |
| search worker | `services/search/` | splits Markdown into chunks at headings, keeps the vector index, searches it, builds answers that cite only real excerpts |
| ollama | image `ollama/ollama` | runs the models; downloads them at the first start |

## Jobs and the GPU queue

Long work (an answer, an index rebuild, a built-in test) is a **job**: the `POST` returns `202` with a job
id, and the client follows it with `GET /v1/jobs/{id}` or the event stream
`GET /v1/jobs/{id}/events` (Server-Sent Events: `status`, `progress`, `sources`, `token`, `check`,
`result`, `error`). `?wait=true` turns any job into a plain blocking call.

GPU jobs wait in **one queue, one at a time**. On a 12 GB card a speech model, a voice model and an LLM
do not fit together; a queue is simpler and more honest than guessing what fits. The status bar of the
web page and `GET /v1/status` show what is running and how many jobs wait.

Ollama unloads a model after `IDLE_UNLOAD_MINUTES` without use (default 10), so VRAM goes back to the
system when the stack is idle.

## The worker contract

Every worker answers the same internal calls, so the gateway needs no list written by hand:

| Call | Answer |
|---|---|
| `GET /health` | alive |
| `GET /info` | the services it offers: model, VRAM estimate, license, profile |
| `POST /selftest` | its built-in test, as a stream of checks |
| service calls | here `/search`, `/ask`, `/index`, streaming NDJSON events |

`GET /v1/services` is the sum of every worker's `/info`, with the live state added (model present,
loaded, VRAM in use, last test).

## Data

| Volume | Content |
|---|---|
| `ollama` | the models |
| `index` | the vector index of the notes (rebuilt from the notes at any time) |
| `gateway-data` | the result of the last built-in test of each service |
| `uploads` | files being converted, deleted when their job ends |
| `library` | converted documents you chose to keep; searched with your notes as `library/…` |
| `documents-models` | Docling's models |
| `transcriber-models`, `speakers-models` | the Whisper and ECAPA models |
| `NOTES_DIR` | your notes, mounted read-only, never written |

Nothing is sent anywhere: no telemetry, and the models are downloaded from the Ollama registry only.

## How a question is answered

```mermaid
sequenceDiagram
    participant C as client
    participant G as gateway
    participant S as search worker
    participant O as ollama
    C->>G: POST /v1/ask
    G-->>C: 202 job id
    G->>S: /ask (in the GPU queue)
    S->>S: index up to date? re-embed changed files only
    S->>O: embed the question (bge-m3)
    S->>S: best chunks, at most 2 per file
    alt no chunk above the threshold
        S-->>G: "I can't find anything" (model not called)
    else
        S->>O: chat with the numbered excerpts (qwen3.5:9b)
        O-->>S: tokens
        S->>S: check every [n] against the excerpts
    end
    S-->>G: sources, tokens, result
    G-->>C: events (SSE) or the result
```
