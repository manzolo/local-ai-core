"""Search worker: semantic search over a folder of Markdown notes, and answers that cite them.

Internal API, reachable only on the compose network (the gateway is the public face):
  GET  /health      liveness
  GET  /info        what this worker offers: services, models, VRAM estimate, licenses
  POST /index       bring the index up to date (NDJSON progress stream)
  POST /search      best chunks for a query
  POST /ask         answer from the chunks, citations checked (NDJSON stream: sources, tokens, result)
  POST /selftest    known data → expected output, plus introduced errors that must be caught (NDJSON)
"""
import asyncio
import json
import os
import shutil
import tempfile
import time

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import ask as askmod
from . import index as idx
from .ollama import Ollama, OllamaError

VERSION = "0.1.0"
HERE = os.path.dirname(os.path.abspath(__file__))


def env(name, default):
    return os.environ.get(name, default)


class Settings:
    def __init__(self):
        self.ollama_host = env("OLLAMA_HOST", "http://ollama:11434")
        self.embed_model = env("EMBED_MODEL", "bge-m3")
        self.llm_model = env("OLLAMA_MODEL", "qwen3.5:9b")
        self.notes_dir = env("NOTES_DIR", "/notes")
        self.index_dir = env("INDEX_DIR", "/data/index")
        self.min_score = float(env("SEARCH_MIN_SCORE", "0.35"))
        self.keep_alive = f"{int(env('IDLE_UNLOAD_MINUTES', '10'))}m"

    @property
    def index_base(self):
        return os.path.join(self.index_dir, "notes")


app = FastAPI(title="local-ai-core search worker", version=VERSION)
app.state.settings = Settings()
app.state.transport = None  # tests put a fake Ollama here
app.state.lock = asyncio.Lock()


def S():
    return app.state.settings


def ollama():
    return Ollama(S().ollama_host, keep_alive=S().keep_alive, transport=app.state.transport)


def ndjson(gen):
    async def body():
        try:
            async for item in gen:
                yield json.dumps(item, ensure_ascii=False) + "\n"
        except (OllamaError, idx.IndexError_) as e:
            yield json.dumps({"type": "error", "message": str(e)}) + "\n"
    return StreamingResponse(body(), media_type="application/x-ndjson")


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/info")
async def info():
    s = S()
    return {
        "worker": "search", "version": VERSION, "profile": "default",
        "services": [
            {"name": "search", "title": "Semantic search",
             "description": "Search your Markdown notes by meaning, not by exact words.",
             "model": s.embed_model, "vram_gb": 2, "license": "MIT",
             "license_url": "https://huggingface.co/BAAI/bge-m3", "endpoints": ["/v1/search", "/v1/search/index"]},
            {"name": "ask", "title": "Ask your notes",
             "description": "Answers built only from the chunks the search finds, every citation checked.",
             "model": s.llm_model, "vram_gb": 8, "license": "Apache-2.0",
             "license_url": "https://ollama.com/library/qwen3.5", "endpoints": ["/v1/ask"]},
        ],
    }


async def ensure_index(o, root, base, model, rebuild=False, progress=None):
    async with app.state.lock:
        return await idx.update(o, root, base, model, rebuild=rebuild, progress=progress)


class IndexReq(BaseModel):
    rebuild: bool = False


@app.post("/index")
async def index(req: IndexReq):
    s = S()
    if not os.path.isdir(s.notes_dir):
        raise HTTPException(400, f"notes folder not found: {s.notes_dir}")

    async def gen():
        q: asyncio.Queue = asyncio.Queue()

        async def progress(done, total):
            await q.put({"type": "progress", "done": done, "total": total})

        task = asyncio.create_task(ensure_index(ollama(), s.notes_dir, s.index_base, s.embed_model,
                                                req.rebuild, progress))
        while not task.done() or not q.empty():
            try:
                yield await asyncio.wait_for(q.get(), 0.2)
            except TimeoutError:
                pass
        _, _, stats = task.result()
        yield {"type": "result", **stats}
    return ndjson(gen())


class SearchReq(BaseModel):
    q: str = Field(min_length=1, max_length=2000)
    n: int = Field(8, ge=1, le=50)
    min: float | None = None
    update: bool = True


async def run_search(o, q, n, min_score, update=True):
    s = S()
    if update:
        meta, vec, _ = await ensure_index(o, s.notes_dir, s.index_base, s.embed_model)
    else:
        got = idx.load(s.index_base)
        if not got:
            raise HTTPException(409, "the index does not exist yet: index the notes first")
        meta, vec = got
    if not meta["chunks"]:
        return []
    return await idx.query(o, meta, vec, q, n, s.min_score if min_score is None else min_score)


@app.post("/search")
async def search(req: SearchReq):
    try:
        return {"results": await run_search(ollama(), req.q, req.n, req.min, req.update)}
    except idx.IndexError_ as e:
        raise HTTPException(409, str(e)) from None
    except OllamaError as e:
        raise HTTPException(502, str(e)) from None


class AskReq(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    n: int = Field(6, ge=1, le=20)
    min: float | None = None
    model: str | None = None


async def answer_stream(o, question, sources, model):
    """The ask pipeline after the search: yields token events, then the result."""
    if not sources:
        yield {"type": "result", **askmod.result(askmod.NOT_FOUND, [], model, 0)}
        return
    t0, pieces, end = time.time(), [], {}
    async for d in o.chat_stream(model, askmod.SYSTEM, askmod.build_prompt(question, sources)):
        p = d.get("message", {}).get("content", "")
        if p:
            pieces.append(p)
            yield {"type": "token", "text": p}
        if d.get("done"):
            end = d
    text = "".join(pieces).strip()
    if not end:
        raise OllamaError("connection closed before the end of the answer")
    if not text:
        raise OllamaError(f"empty answer (stop reason: {end.get('done_reason')})")
    yield {"type": "result", **askmod.result(text, sources, model, round(time.time() - t0, 1),
                                             truncated=end.get("done_reason") == "length")}


@app.post("/ask")
async def ask(req: AskReq):
    s = S()
    o = ollama()
    model = req.model or s.llm_model

    async def gen():
        try:
            sources = await run_search(o, req.question, req.n, req.min)
        except HTTPException as e:
            yield {"type": "error", "message": e.detail}
            return
        yield {"type": "sources", "sources": sources}
        async for ev in answer_stream(o, req.question, sources, model):
            yield ev
    return ndjson(gen())


# ------------------------------------------------------------------------------------------ self-test

SELFTEST_SEARCH = [
    ("the heating stops and shows a fault code", "projects/pellet-stove.md"),
    ("how much did I spend on the water leak", "projects/bathroom.md"),
    ("vegetables planted in the yard", "journal/garden.md"),
]


async def selftest_checks(o):
    """Known notes in a scratch folder, never the user's: three questions with words different from the
    notes, an incremental update, a truncated index that must be refused, a grounded answer, a question
    with no answer, and an invented citation that must be flagged."""
    s = S()
    tmp = tempfile.mkdtemp(prefix="selftest-")
    try:
        root, base = os.path.join(tmp, "notes"), os.path.join(tmp, "index", "notes")
        shutil.copytree(os.path.join(HERE, "selftest_notes"), root)

        def check(name, ok, detail):
            return {"type": "check", "name": name, "ok": bool(ok), "detail": detail}

        meta, vec, _ = await idx.update(o, root, base, s.embed_model)
        for q, want in SELFTEST_SEARCH:
            got = await idx.query(o, meta, vec, q, 1, 0)
            top = got[0]["file"] if got else "-"
            yield check(f"search: {q}", top == want, f"top result {top} (expected {want})")

        with open(os.path.join(root, "journal", "garden.md"), "a", encoding="utf-8") as f:
            f.write("\nThe courgettes caught powdery mildew.\n")
        _, _, stats = await idx.update(o, root, base, s.embed_model)
        yield check("incremental update", stats["changed"] == 1 and stats["kept"] == 2,
                    f"{stats['changed']} re-read, {stats['kept']} unchanged (expected 1 and 2)")

        broken = os.path.join(tmp, "broken")
        shutil.copy(base + ".json", broken + ".json")
        shutil.copy(base + ".f32", broken + ".f32")
        os.truncate(broken + ".f32", os.path.getsize(broken + ".f32") - 64)
        try:
            idx.load(broken)
            yield check("introduced error: truncated index", False, "a truncated index was accepted")
        except idx.IndexError_ as e:
            yield check("introduced error: truncated index", True, f"refused: {e}")

        meta, vec, _ = await idx.update(o, root, base, s.embed_model)
        q = "how much did I pay to fix the leak in the bathroom?"
        res = await _collect(o, q, await idx.query(o, meta, vec, q, 6, s.min_score), s.llm_model)
        first = res["cited"][0]["file"] if res["cited"] else "-"
        yield check("ask: grounded answer", "180" in res["answer"] and first == "projects/bathroom.md",
                    f"{res['answer'][:160]} (first source {first})")

        q = "what is the name of the neighbour's cat?"
        res = await _collect(o, q, await idx.query(o, meta, vec, q, 6, 0), s.llm_model)
        a = res["answer"].lower()
        said_no = any(w in a for w in ("can't find", "cannot find", "not find", "no information", "not mention",
                                       "doesn't mention", "do not mention", "don't mention", "not contain",
                                       "doesn't contain", "don't contain", "no mention", "not in the notes"))
        yield check("ask: question with no answer", said_no and "180" not in a, res["answer"][:160])

        canned = "The invoice was 180 euros [1], paid by the neighbour [9]."
        cited, invalid = askmod.citations(canned, 6)
        yield check("introduced error: invented citation", invalid == [9] and cited == [1],
                    f"cited {cited}, flagged {invalid}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def _collect(o, question, sources, model):
    out = {}
    async for ev in answer_stream(o, question, sources, model):
        if ev["type"] == "result":
            out = ev
    return out


@app.post("/selftest")
async def selftest():
    async def gen():
        ok = True
        async for c in selftest_checks(ollama()):
            ok = ok and c["ok"]
            yield c
        yield {"type": "result", "ok": ok}
    return ndjson(gen())
