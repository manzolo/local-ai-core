# Troubleshooting

[← back to the index](../README.md)

Start from `docker compose ps` (is everything `Up` and `healthy`?) and `docker compose logs --tail 50`.

## The GPU is not visible in containers

`could not select device driver "" with capabilities: [[gpu]]` means Docker does not know the NVIDIA
runtime. Install the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html),
then:

```bash
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
docker run --rm --gpus all ubuntu:24.04 nvidia-smi
```

## The page shows "Downloading model" for a long time

The two models weigh about 8 GB. The banner shows the progress; `docker compose logs -f ollama` shows the
download. If the download fails (no network), the banner turns red and the gateway retries every 30 s.

## "Waiting for the engine (Ollama)…" never ends

- standalone: `docker compose logs ollama`. A GPU error there means the toolkit problem above;
- with the family override: is `ollama-docker` running, and is its container called `ollama` on
  `local-ai-net`? `docker network inspect local-ai-net` lists who is attached.

## Port 8090 is already taken

Set another one in `.env`: `GATEWAY_PORT=8091`, then `docker compose up -d`.

## Out of GPU memory

`GET /v1/status` (or the status bar) shows what holds the VRAM. Ollama frees a model after
`IDLE_UNLOAD_MINUTES`; to free it now:

```bash
docker compose exec ollama ollama stop qwen3.5:9b
```

On a GPU with less than 8 GB, use a smaller model: `OLLAMA_MODEL=qwen3.5:4b` in `.env`.

## The Documents tab says the service is off

It runs in the `docs` profile: `docker compose --profile docs up -d` (or `COMPOSE_PROFILES=docs` in
`.env`). The first build downloads PyTorch with CUDA: about 7 GB.

## The Build / Start / Stop buttons do not appear

They need the `control` profile: `docker compose --profile control up -d`, run from the project folder. On a
gateway opened to the network (`BIND_ADDR=0.0.0.0`) they also need `LOCAL_AI_API_KEY`.

## A button says "build it first" or fails in family mode

Start needs the image: press **Build** first. In family mode the buttons must use the same compose files as
your stack: `COMPOSE_FILE=docker-compose.yml:docker-compose.local-ai.yml` in `.env`.

## The voice says its license is not accepted

That is on purpose: XTTS-v2 is for non-commercial use only, and only you can accept that. Read
<https://huggingface.co/coqui/XTTS-v2/blob/main/LICENSE.txt>, write `XTTS_ACCEPT_CPML=yes` in `.env`, then `docker compose --profile voice
up -d`.

## A voice piece still does not say its text

The result lists it in `verify.below` after three tries. Rewrite it more plainly (spell out a strange name,
split a long sentence), or try another voice.

## An image job says a model's sha256 does not match

The download was cut or altered: the file was discarded. Run the job again; if it keeps failing, the
official file may have changed, which the project must check before using it.

## A worker restarts by itself

That is how a GPU worker gives back its memory when another job needs it, or after
`IDLE_UNLOAD_MINUTES`: its container exits and Docker starts it again in a few seconds. `docker compose ps`
shows a short uptime; nothing is lost.

## "Who said what" is greyed out

It needs the `speakers` profile: `docker compose --profile transcribe --profile speakers up -d`.

## Two people are found as one (or one as two)

If you know how many people speak, say so: `num_speakers` in the API. Very short sentences (under 0.6 s)
carry too little voice and take the speaker of the nearest sentence.

## A scanned PDF comes back empty

`ocr=off` never reads scans, by design. With `auto`, a PDF that has a broken text layer over the scan
looks like text to Docling: use `ocr=force`.

## "index is inconsistent"

The index files do not match: fewer vectors than chunks, or vectors that are not the ones saved with those
chunks (a disk filled up or the machine stopped during a write, for example). It is refused instead of
searched, because it would answer with the wrong notes without saying so. Rebuild it: **Search →
Rebuild index**, or `curl -s -X POST 'http://localhost:8090/v1/search/index?wait=true' -H 'Content-Type: application/json' -d '{"rebuild": true}'`.

## The answer says it can't find something that is in my notes

- the note must be a `.md` file inside `NOTES_DIR` (hidden folders are skipped);
- look at **Search** with the same question: if the note is not in the first results, lower
  `SEARCH_MIN_SCORE` (e.g. `0.3`) or ask with words closer to the note's.
