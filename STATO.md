# Stato di local-ai-core

Aggiornato: 2026-11-22 (sessione 1). **Fase: proposta di architettura, niente codice ancora.**

## Cosa funziona

Niente: il repo contiene solo questa pagina. La cartella è stata inizializzata con git, senza remoto.

## Architettura proposta

```
browser ──► gateway (FastAPI, 127.0.0.1:8090) ──► worker interni ──► Ollama
            /v1/… · /openapi.json · /docs · UI     (rete di compose)   (suo o di local-ai-net)
```

- **Gateway** (`gateway/`): l'unico con una porta pubblicata, su `127.0.0.1:8090`. Serve l'API `/v1/…`,
  l'OpenAPI, la pagina web statica. Tiene i **job** (`POST` → `202` + `job_id`, `GET /v1/jobs/{id}`,
  avanzamento in SSE con polling di riserva) e la **coda GPU**: un lavoro pesante alla volta. Prima di
  avviare un worker pesante chiede a Ollama di scaricare i modelli (`keep_alive: 0`).
- **Worker** (`services/<nome>/`): un container per servizio, mai esposto. Contratto uguale per tutti:
  `GET /health`, `GET /info` (nome, modello, versione, VRAM stimata, licenza, stato), `POST /run`,
  `POST /unload`. Il modello si carica al primo uso e si scarica dopo `IDLE_UNLOAD_MINUTES` (default 10).
  Il catalogo `GET /v1/services` è la somma degli `/info`: nessun elenco scritto a mano.
- **Profili di compose**: senza profilo partono gateway, Ollama, search e ask. Poi `docs`, `transcribe`,
  `image`, `handwriting`, e `voice` (XTTS, non commerciale: spento finché non lo si chiede).
- **Ollama**: il compose di base ne porta uno suo, fissato a `0.40.16`, **senza porta pubblicata e senza
  `container_name`**, così non urta un Ollama già acceso sull'host. Con
  `docker-compose.local-ai.yml` quello interno si toglie (`!reset`) e il gateway entra in `local-ai-net`
  con `OLLAMA_HOST=http://ollama:11434`.
- **Modelli**: mai nel repo. Un servizio `models-init` scarica al primo avvio `bge-m3` e il modello di
  `OLLAMA_MODEL` (default `qwen3.5:9b`); la pagina web mostra lo scaricamento.
- **Dati**: volumi `models`, `index`, `uploads` (svuotato a lavoro finito); `NOTES_DIR` montata in sola
  lettura, di default `examples/notes` (note sintetiche in inglese).
- **Ricerca**: pezzi tagliati ai titoli, vettori `float32` su file, aggiornamento incrementale per sha1,
  indice incoerente rifiutato. **Domande**: risposta solo dai pezzi trovati, citazioni `[n]` controllate
  contro i pezzi, «not found» senza chiamare il modello se la ricerca è vuota.
- **Interfaccia**: una pagina, HTML + JS senza build e senza CDN, chiara e scura, inglese e italiano.
  Usa solo `/v1/…`.
- **Sicurezza**: ascolto su `127.0.0.1`, `LOCAL_AI_API_KEY` facoltativa, `MAX_UPLOAD_MB`, niente telemetria.
- **Versioni**: immagini base per digest, `constraints.txt` per pip.
- **Test**: `pytest` in GitHub Actions su CPU con Ollama e worker finti, ognuno con un errore introdotto
  che deve essere colto; `make collaudo` per la prova vera sulla GPU.
- **Documentazione**: `README.md` è l'indice; le sezioni stanno in `docs/`, una per file. La prima è
  `docs/quick-start.md`, la guida per partire da zero: ogni fase vi aggiunge il suo passo e si riesegue
  alla lettera su un clone pulito. Le schermate si fanno durante la prova (`make screenshots`, Chromium
  headless in container) e mostrano solo dati sintetici.

## Scelte prese come default (si cambiano se il maintainer dice di no)

| Scelta | Default | Perché |
|---|---|---|
| search e ask | un solo worker leggero `services/search`, due voci nel catalogo | ask dipende da search, tutti e due chiamano solo Ollama |
| Job | in memoria nel gateway, risultati su volume | basta alla fase 1; un riavvio perde i job in corso |
| GPU | il compose di base chiede la GPU NVIDIA | è il requisito dichiarato; la CI usa worker finti |
| Lingua di questa pagina | italiano | è il quaderno di lavoro; README e codice in inglese |
| Nomi | repo `local-ai-core`, servizio compose `gateway`, OpenAPI «Local AI Core» | deciso dal maintainer il 2026-11-22 |
| Licenza del codice | MIT | come da brief; licenze dei modelli nel README, una per una |
| Brief di partenza | `PROMPT.md` fuori da git (`.gitignore`) | contiene percorsi e nomi privati |

## Scelte aperte

1. `documents → search → ask` sostituisce `ollapdf` e `PDF-Chatbot-Ollama-Langchain-Docker` o li
   affianca? Si decide alla fase 2.
2. Licenze dei checkpoint immagine (SDXL, Juggernaut, LoRA Lightning) e dell'audio di prova generato con
   XTTS: da verificare alle fasi 4 e 5.

## Cosa manca

Tutto il codice. Le fasi sono quelle del brief: 1 scheletro con search + ask, 2 documenti,
3 trascrizione e parlanti, 4 immagini, 5 voce e scrittura a mano, 6 rilascio (solo col sì del maintainer).

## Prossimo passo

Fase 1. Criterio: `git clone && docker compose up -d`, poi
`http://localhost:8090` risponde a una domanda citando le note di esempio.
