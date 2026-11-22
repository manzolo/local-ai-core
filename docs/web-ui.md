# Web interface

[← back to the index](../README.md)

One page at **<http://localhost:8090>**, served by the gateway itself: no CDN, no fonts or scripts from
elsewhere, so it works offline. It uses only the public `/v1` API, the same one documented at `/docs`.

| Light | Dark |
|---|---|
| ![Light theme](img/ask-light.png) | ![Dark theme](img/ask-dark.png) |

## What is on the page

- **Status bar**: the GPU and its VRAM, the models Ollama holds in memory right now, the job queue, the
  engine. It refreshes every 3 seconds.
- **Ask**: a question about your notes. The answer streams as it is written; each `[n]` links to its
  excerpt below, and the cited excerpts are highlighted. A citation that matches no excerpt shows in red
  with a warning.
- **Search**: search by meaning, with the score and the headings above each hit. **Rebuild index**
  reads every file again.
- **Documents** (profile `docs`): drop a PDF, image or Office file; the Markdown appears with its
  tables, can be downloaded, and with *Add to the library* becomes something **Ask** can cite.
- **Transcribe** (profiles `transcribe`, `speakers`): drop an audio or video file; the sentences appear
  with their time and speaker, the progress follows the seconds of audio, and the transcript downloads as
  TXT, SRT or WebVTT. *Check it says a given text* scores the transcript against an expected text.
- **Images** (profile `image`): *Generate*, *Upscale* or *Cut out*; the result shows on a checkerboard,
  so transparency is visible, and downloads as PNG. The license note of SDXL stays under it.
- **Handwriting** (profile `handwriting`): drop a photo of a page; the text appears, to copy or download.
- **Voice** (profile `voice`): a red notice stays until the non-commercial license of XTTS-v2 is accepted;
  then type a text, pick a voice and a language, and listen; the result says whether every piece was
  listened to again and still says its text.
- **Services**: each service with its model, VRAM, license and last test. **Run test** runs its built-in
  test and shows every check as it completes.

![Search](img/search.png)

![Documents](img/documents.png)

![Transcribe](img/transcribe.png)

![Images](img/images-generate.png)

![Handwriting](img/handwriting.png)

![Services and a test run](img/services.png)

## Language, theme, phone

The page follows the browser's language (English or Italian) and the system theme; the two buttons at
the top right switch them, and the choice is remembered in this browser. It works at phone width:

<img src="img/phone.png" alt="The page on a phone" width="300">

The page can also be opened on a given tab, theme and language: `/?tab=search&theme=dark&lang=it`.

## With an API key

When `LOCAL_AI_API_KEY` is set, the page asks for the key once and keeps it in this browser
(`localStorage`).
