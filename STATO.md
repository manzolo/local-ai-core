# Stato di local-ai-core

Aggiornato: 2026-11-22 (sessione 1). **Fasi 1 e 2 completate e collaudate. Prossima: fase 3, trascrizione e parlanti.**
Repo su GitHub `manzolo/local-ai-core`, **pubblico dal 2026-11-22** (deciso dal maintainer), CI verde.
Nome ed email del maintainer (anche come autore dei commit) sono pubblici di proposito: va tenuto fuori
tutto il resto (strumenti e note personali, percorsi, macchine, lavoro, documenti).

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
- **Documenti (fase 2)**, profilo `docs`: Docling 2.151.1 sul vero, collaudo interno 5/5 (PDF col
  testo, scansione con OCR, cifra alterata colta, scansione senza OCR rifiutata, PDF rotto). Catena
  completa provata: scansione storta → Markdown con la tabella giusta → libreria → domanda → risposta
  che cita `library/…` → rimozione. Quick start passo 9 rifatto da un clone pulito.
- **Test su CPU**: 34 test con Ollama e Docling finti, lint pulito, 12 mutazioni tutte colte.
- **CI su GitHub** verde (test, lint, mutazioni, privacy, compose e build delle immagini leggere).
- **Schermate** in `docs/img/` (`make screenshots`), riguardate una per una: solo note di esempio.
- Gateway e worker leggeri: immagini da ~140 MB; documenti ~7 GB (PyTorch con CUDA), modelli ~540 MB in
  un volume. Utente non root, nessuna porta dei worker pubblicata.

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
- **documents** (profilo `docs`): Docling dietro un'interfaccia piccola, caricato al primo uso e scaricato
  dopo `IDLE_UNLOAD_MINUTES`; i file caricati stanno nel volume `uploads` e si cancellano a fine job; il
  Markdown va nel volume `library` solo se richiesto. Prima di un worker GPU il gateway chiede a Ollama
  di liberare i modelli se la VRAM libera non basta.
- **search + ask** in un solo worker leggero: porting ripensato di `search.py` e `ask.py`
  (note + libreria, la libreria col prefisso `library/`; pezzi ai titoli, indice float32 incrementale per sha1, indice incoerente rifiutato, citazioni
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
| Nome ed email del maintainer | ammessi nel repo e nei commit | deciso dal maintainer il 2026-11-22 |
| Bolletta di prova | nuova, inventata, in inglese (`examples/documents/invoice.html`), PDF e «scansione» generati da `scripts/make-fixtures.sh` | il brief chiede di rigenerarla |
| Documenti nella libreria | stesso nome = sostituito; un file di note chiamato `library/…` si confonderebbe col prefisso | semplice; documentato |
| Lingua OCR | una per richiesta, default `en` | RapidOCR ne regge una alla volta |

## Da fare prima della pubblicazione (fase 6)

- Licenze: verificate una per una il 2026-11-22 (`docs/licenses.md`, `docs/licenses-python.md` generato
  da `scripts/licenses.py`). Da rifare a ogni modello o dipendenza nuova.
- Topic `local-ai`, riga in `local-ai-lab`, scheda su manzolo-home: solo col sì del maintainer.

## Scelte aperte

1. `documents → search → ask` sostituisce `ollapdf` e `PDF-Chatbot-Ollama-Langchain-Docker` o li
   affianca? Domanda posta al maintainer a fine fase 2.
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
- L'OCR su una scansione perde qualche spazio («invoiceno.2093», «Meterreading»): cifre e tabella sono
  giuste, ed è su quelle che si basa il collaudo.
- Starlette 1.x non ha più `@app.on_event`: si usa `lifespan`.
- Un volume nuovo prende proprietario e permessi dalla cartella dell'immagine: `/library` e
  `/data/uploads` vanno create nelle immagini con l'utente non root, se no diventano di root.
- `git push` dalla sessione: va fuori dalla sandbox (la chiave SSH sta nell'agente del desktop).
- Prima della pubblicazione la cronologia è stata riscritta (`git filter-repo`) per togliere una parola
  privata da due vecchie versioni di questo file; push forzato approvato dal maintainer. `grep-private.sh
  --history` segnala ancora `10.4.0.35` in un commit: è il numero di versione di `nvidia-curand`, non un
  indirizzo.
- `gh` 2.46 non ha `--accept-visibility-change-consequences`: la visibilità si cambia con
  `gh api -X PATCH repos/OWNER/REPO -f visibility=public`.
- In modalità famiglia `down -v` non cancella il volume dell'Ollama interno (il servizio è spento).
- Il volume `local-ai-core_ollama` tiene i modelli dell'Ollama interno (~8 GB) anche a stack spento:
  `docker compose down -v` lo toglie.

## Prossimo passo

Fase 3: worker `transcriber` (faster-whisper 1.2.1, large-v3, audio decodificato con ffmpeg) e
`speakers` (SpeechBrain 1.1.2 ECAPA, soglia 0,55) nel profilo `transcribe`; `POST /v1/transcribe` con
testo, srt, vtt e `speakers=true`; avanzamento vero via SSE. Audio di prova sintetico **non** XTTS
(licenza): da scegliere una voce con licenza libera. Sezione `docs/services/transcribe.md`, passo nel
quick start, schermate.
