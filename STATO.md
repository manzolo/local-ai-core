# Stato di local-ai-core

Aggiornato: 2026-11-22 (sessione 1). **Fasi 1-4 completate e collaudate. Prossima: fase 5, scrittura a mano e voce.**
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
- **Trascrizione e parlanti (fase 3)**, profili `transcribe` e `speakers`: faster-whisper large-v3 e
  SpeechBrain ECAPA sul vero, collaudi interni 5/5 e 4/4 (frase nota con somiglianza 1,00; dialogo a
  due voci 121212; una voce resta una; testo diverso e file non audio colti). Audio di prova sintetico
  con Piper e due voci di pubblico dominio (`scripts/make-audio-fixtures.sh`).
- **Prova da zero completa** (2026-11-22): 0 volumi, clone pulito, quick start passi 3, 9, 10: Ollama
  scarica i modelli e indicizza in 134 s; collaudo con tutti i profili superato in 243 s, scaricando
  anche Docling, Whisper (2,9 GB) ed ECAPA.
- **Immagini (fase 4)**, profilo `image`: ComfyUI v0.46.2 dietro un adattatore; SDXL base + LoRA
  Lightning (1-2 s a modello caricato), RealESRGAN ×2/×4, scontorno BiRefNet. Collaudo interno 5/5 sul
  vero (maschera non invertita colta). Modelli dalle fonti ufficiali, sha256 controllato.
- **GPU condivisa, misurata sul vero**: un'immagine fa scaricare documenti, trascrittore e parlanti (che
  si riavviano e tornano a 0 MiB); una domanda fa fermare ComfyUI (0 MiB) e risponde con la fonte.
- **Prova da zero della fase 4** (commit `325c8b9`): 0 volumi, clone pulito, quick start passi 3 e 11,
  collaudo superato in 416 s scaricando ~15 GB di modelli. La prima prova da zero aveva trovato un
  esaurimento di memoria nello scontorno del collaudo: corretto (vedi trappole) prima del push.
- **Test su CPU**: 68 test con Ollama, Docling, Whisper, ECAPA e ComfyUI finti (il ComfyUI finto esegue
  i workflow con Pillow seguendo le regole dei nodi veri), lint pulito, 26 mutazioni tutte colte.
- **CI su GitHub** verde (test, lint, mutazioni, privacy, compose e build delle immagini leggere).
- **Schermate** in `docs/img/` (`make screenshots`), riguardate una per una: solo note di esempio.
- Gateway e worker leggeri: immagini da ~140 MB; documenti ~7 GB e parlanti ~6,6 GB (PyTorch con CUDA
  come primo strato identico, salvato una volta sola); trascrittore ~3,4 GB (CTranslate2, niente torch). Utente non root, nessuna porta dei worker pubblicata.

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
- **transcriber** (profilo `transcribe`) e **speakers** (profilo `speakers`): due worker; il gateway
  li concatena nello stesso job di `/v1/transcribe` e compone testo, SRT e WebVTT. `expect` confronta la
  trascrizione con un testo atteso (somiglianza per parole, soglia 0,85).
- **image** (profilo `image`): l'adattatore MIT avvia ComfyUI (GPL-3.0, non modificato, commit fissato)
  come processo figlio su 127.0.0.1 e lo ferma dopo l'inattività o su `/unload`; fra lavori di tipo
  diverso chiede a ComfyUI di scaricare i modelli. I PNG stanno in `gateway-data/results` e se ne vanno
  col job (`GET /v1/jobs/{id}/file`).
- **VRAM**: `ensure_vram` prima di ogni job GPU (ask e collaudi compresi) libera Ollama (tranne il
  modello che serve) e chiede `/unload` agli altri worker; un worker GPU che si scarica **esce** e
  Docker lo riavvia pulito (`RESTART_TO_FREE_VRAM`), il gateway aspetta fino a 30 s un worker in riavvio.
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
| Checkpoint immagini | SDXL base 1.0 ufficiale, non Juggernaut | Juggernaut nel coltellino arrivava da un mirror senza licenza; `IMAGE_CHECKPOINT` per cambiarlo |
| RealESRGAN | dalla release ufficiale su GitHub (BSD-3) | il mirror del coltellino dichiarava «other» |
| Liberare VRAM | fermare il processo (ComfyUI) o riavviare il container (gli altri) | `/free` e `empty_cache` lasciano contesto CUDA e cache: misurati 0,3-1 GB per worker |

## Da fare prima della pubblicazione (fase 6)

- Licenze: verificate una per una il 2026-11-22 (`docs/licenses.md`, `docs/licenses-python.md` generato
  da `scripts/licenses.py`). Da rifare a ogni modello o dipendenza nuova.
- Scheda su manzolo-home: da proporre, non toccare (altro progetto).

## Scelte aperte

1. Deciso col maintainer il 2026-11-22: PDF-Chatbot-Ollama-Langchain-Docker **archiviato**, sostituito
   da local-ai-core (nota in testa al suo README); **ollapdf affiancato** (chat Streamlit + LangChain),
   da rivalutare dopo qualche settimana d'uso. README di local-ai-lab aggiornato, topic `local-ai` messo.
2. Licenza di XTTS (fase 5): solo uso non commerciale, profilo a parte spento di default.

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
- **zsh non spezza le variabili non quotate**: `ALL="--profile a --profile b"; docker compose $ALL down`
  passa un solo argomento e fallisce. Nei comandi lunghi scrivere i profili per esteso e leggere l'output
  invece di contarlo con `grep -c`: una prova «da zero» è sembrata riuscita su container vecchi.
- Lo strato PyTorch è condiviso solo se le righe del Dockerfile fino a `pip install -r torch.txt` sono
  identiche in documents e speakers: l'`ARG DEBIAN_SNAPSHOT` va dopo.
- **Esaurimento di memoria nello scontorno**: con SDXL ancora in ComfyUI e bge-m3 in Ollama, BiRefNet
  (che lavora a 1024×1024) non ci stava. Era passato nella prima prova solo perché la GPU era più vuota:
  l'ha trovato la prova da zero. Rimedi: `/free` di ComfyUI fra lavori di tipo diverso, e `ensure_vram`
  anche nei collaudi.
- Docker non applica il riavvio a `docker compose down`: un worker uscito per liberare VRAM riparte solo
  per `restart: unless-stopped`.
- `Literal[2, 4]` di FastAPI non converte i valori dei form (`"4"`): usare un intero con limiti.
- Il visualizzatore di immagini dell'agente ignora l'alfa: la trasparenza si misura sui pixel.
- Il volume `local-ai-core_ollama` tiene i modelli dell'Ollama interno (~8 GB) anche a stack spento:
  `docker compose down -v` lo toglie.

## Prossimo passo

Fase 5: `handwriting` (profilo `handwriting`): `qwen3-vl:8b-instruct` su Ollama, mai la variante
thinking; `POST /v1/handwriting`; foto di prova sintetiche (scrittura generata, niente appunti veri).
`voice` (profilo `voice`, **spento di default**): XTTS-v2 (coqui-tts 0.27.5), **solo uso non
commerciale** (Coqui Public Model License) scritto nel README, nel catalogo e nella pagina; testi lunghi
a pezzi, numeri in lettere, riascolto col trascrittore; solo voci pronte del modello, nessun campione
personale. Verificare prima le licenze (qwen3-vl Apache-2.0 già vista).
