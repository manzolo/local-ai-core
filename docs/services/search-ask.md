# Search and Ask

[← back to the index](../../README.md)

Two services in one worker (`services/search`), on by default:

| Service | Endpoint | Model | VRAM |
|---|---|---|---|
| **Search** — find notes by meaning, not by exact words | `GET /v1/search`, `POST /v1/search/index` | bge-m3 | ~2 GB |
| **Ask** — answers built only from the notes found, every citation checked | `POST /v1/ask` | qwen3.5:9b | ~8 GB |

## How the notes are read

- every `.md` file under `NOTES_DIR`, skipping hidden folders and `.git`, `node_modules`, `.obsidian`,
  `.trash`;
- front matter (`--- … ---`) is a short chunk of its own;
- the text is cut at headings, then at paragraphs, about 1200 characters per chunk;
- each chunk carries the file title and the headings above it: a chunk under *Next step* knows what it
  is the next step of;
- the index keeps a sha1 per file and reads again only the files that changed.

## How a search ranks

Cosine similarity between the question and each chunk, plus a small push (at most +0.05) for chunks that
contain the question's exact words, which helps with names and codes. At most two chunks per file, so
the results cover more notes. Hits under `SEARCH_MIN_SCORE` (0.35) are dropped.

## How an answer stays honest

1. no chunk above the threshold → *I can't find anything about this in the notes*, and the model is not
   called at all;
2. the model receives the chunks numbered `[1]…[n]` and the rule to answer only from them, cite each
   statement, and say plainly when the notes do not answer;
3. every `[n]` in the answer is checked: a number with no excerpt is reported in `invalid`, the answer is
   marked not `grounded`, and the web page shows a warning.

The language of the answer follows the language of the question.
