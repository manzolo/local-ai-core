# Stato di local-ai-core

Aggiornato: 2026-11-22 (sessione 1). **Fase 1 completata e collaudata. Prossima: fase 2, documenti.**
Niente è pubblicato: repo git locale su `main`, nessun remoto.

## Cosa funziona (provato davvero)

- **Da zero, seguendo il quick start alla lettera** su un clone pulito, volumi cancellati, Ollama vuoto:
  `docker compose up -d` → modelli scaricati e note indicizzate in circa 2 minuti (rete veloce) →
  domanda dalla pagina e con `curl` → risposta giusta con la sua fonte. Passo 6 (`NOTES_DIR` proprio)
  provato con una cartella sintetica.
- **Modalità famiglia** (`docker-compose.local-ai.yml`): l'Ollama interno resta spento, si usa quello di
  `local-ai-net`; il gateway risponde come `http://local-ai-core:8090` dalla rete condivisa.
- **Collaudo GPU** (`make collaudo`) superato in tutte e due le modalità: 8 controlli del collaudo
  interno (3 ricerche con parole diverse dalle note, aggiornamento incrementale, indice troncato
  rifiutato, risposta con fonte, domanda senza risposta, citazione inventata segnalata) + domanda end to end.
- **Test su CPU**: 25 test con Ollama finto, lint pulito, 8 mutazioni tutte colte.
- **Schermate** in `docs/img/` (`make screenshots`), riguardate una per una: solo note di esempio.
- Gateway e worker: immagini da ~140 MB, utente non root, nessuna porta dei worker pubblicata.

## Architettura (com'è adesso)

```
browser ──► gateway (FastAPI, 127.0.0.1:8090) ──► worker search ──► Ollama
            /v1/… · /openapi.json · /docs · UI       (rete compose)    (suo, o di local-ai-net)
```

- **Gateway** (`gateway/localai_gateway/`): API `/v1`, OpenAPI, Swagger UI cotta nell'immagine (niente
  CDN), pagina web statica. Job in memoria con eventi SSE e `?wait=true`; coda GPU con un lavoro alla
  volta; chiave API facoltativa; limite sul corpo delle richieste anche a pezzi. All'avvio aspetta
  Ollama, scarica i modelli che mancano (avanzamento sul modello intero), indicizza le note.
- **Worker** (`services/<nome>/`): contratto `GET /health`, `GET /info`, `POST /selftest` + le chiamate
  del servizio in NDJSON. `GET /v1/services` è la somma degli `/info`.
- **search + ask** in un solo worker leggero: porting ripensato di `search.py` e `ask.py`
  (pezzi ai titoli, indice float32 incrementale per sha1, indice incoerente rifiutato, citazioni
  controllate, «non trovo» senza chiamare il modello). Prompt in inglese, risposta nella lingua della
  domanda.
- **Ollama** `0.40.16` per digest, senza porta pubblicata e senza `container_name`; lo scarico della
  VRAM dopo `IDLE_UNLOAD_MINUTES` lo fa Ollama col suo `keep_alive`.
- **Interfaccia**: una pagina, HTML + JS senza build, chiaro/scuro, inglese/italiano, telefono.
- **Documentazione**: `README.md` è l'indice; sezioni in `docs/`, la prima è `docs/quick-start.md`.

## Scelte prese come default (si cambiano se il maintainer dice di no)

| Scelta | Default | Perché |
|---|---|---|
| Nomi | repo `local-ai-core`, servizio compose `gateway`, OpenAPI «Local AI Core» | deciso dal maintainer il 2026-11-22 |
| search e ask | un solo worker, due voci nel catalogo | ask dipende da search, tutti e due parlano solo con Ollama |
| Contratto dei worker | chiamate specifiche per servizio (`/search`, `/ask`, `/index`) invece di un `POST /run` generico | più leggibile; `/health`, `/info`, `/selftest` restano uguali per tutti |
| Job | in memoria nel gateway (ultimi 200) | basta alla fase 1; un riavvio dimentica i job, i risultati dei collaudi restano su volume |
| Collaudo | dentro ogni worker (`/selftest`), lanciato da `make collaudo`, dall'API e dalla pagina | lo stesso test vale in CI, a mano e dall'interfaccia |
| Gateway e GPU | prenota la GPU solo con capacità `utility` | serve `nvidia-smi` per la barra di stato, zero VRAM |
| Note di esempio | `examples/notes`, inventate, in inglese | default di `NOTES_DIR` e base del quick start |
| Licenza del codice | MIT | da brief; licenze dei modelli in `docs/licenses.md` |
| Brief di partenza | `PROMPT.md` fuori da git | contiene percorsi e nomi privati |
| Nomi privati da cercare | in `.private-patterns`, fuori da git | scriverli nello script pubblico li pubblicherebbe |

## Da fare prima della pubblicazione (fase 6)

- **La cronologia git contiene un nome di persona** nei primi commit di questo file:
  `scripts/grep-private.sh --history` lo mostra. Prima del push: ripartire da un commit unico pulito
  (o riscrivere la cronologia) e rilanciare `--history`.
- Verificare di nuovo una per una le licenze dei modelli e la fonte di ciascuna.
- L'URL del repo nei documenti (`github.com/manzolo/local-ai-core`) esiste solo dopo la pubblicazione.
- La CI di GitHub Actions non è ancora mai girata: la prima esecuzione sarà al primo push.

## Scelte aperte

1. `documents → search → ask` sostituisce `ollapdf` e `PDF-Chatbot-Ollama-Langchain-Docker` o li
   affianca? Si decide alla fine della fase 2.
2. Licenze dei checkpoint immagine (SDXL, Juggernaut, LoRA Lightning) e dell'audio di prova: fasi 4 e 5.

## Note e trappole di questa sessione

- Due Ollama sulla stessa GPU (quello di famiglia con un modello in memoria + il nostro) rallentano molto
  il primo caricamento: la prima risposta ha impiegato 90 s invece di 0,7 s. `docs/family.md` consiglia
  l'override quando ollama-docker è acceso; lo script delle schermate fa una domanda di riscaldamento
  prima di fotografare.
- Ollama riporta l'avanzamento del download **per strato**: va sommato, altrimenti la barra dice
  «0,9 / 0,9 GB» su un modello da 6,6.
- FastAPI trasforma un'eccezione nella lettura del corpo in un `400`: il limite di dimensione intercetta
  anche la risposta, così l'utente vede `413`.
- I test locali sono girati con Python 3.14 (quello dell'host); le immagini e la CI usano 3.12.
- Il volume `local-ai-core_ollama` tiene i modelli dell'Ollama interno (~8 GB) anche a stack spento:
  `docker compose down -v` lo toglie.

## Prossimo passo

Fase 2: worker `documents` (Docling 2.151.1, TableFormer, RapidOCR torch, modelli in un volume) nel
profilo `docs`; `POST /v1/documents/convert` come job; opzione per mandare il Markdown dritto
nell'indice (catena documents → search → ask); bolletta finta rigenerata come prova, con una cifra
alterata come errore introdotto; sezione `docs/services/documents.md`, passo nel quick start, schermate.
