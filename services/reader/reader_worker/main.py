"""Reader worker: photos of handwritten notes → text, with a vision model on Ollama (qwen3-vl).

Internal API, on the compose network only:
  GET  /health      liveness
  GET  /info        the service, its model, VRAM estimate, license
  POST /read        {"path", "expect"?} → NDJSON: progress (model download), result (text, score if expect)
  POST /selftest    an invented handwritten page → its text; a blank page must not be "read" (NDJSON)

What earlier use taught: the "thinking" variant of qwen3-vl reasons forever on a real page and writes
nothing, so the -instruct one is used, with a cap on the answer; the answer is read as a stream; photos
from a phone come turned (EXIF) and too large, so they are straightened and shrunk first.
"""
import base64
import difflib
import io
import json
import os
import re
import tempfile
import time
import unicodedata

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

VERSION = "0.1.0"
HERE = os.path.dirname(os.path.abspath(__file__))
UPLOADS = os.environ.get("UPLOADS_DIR", "/data/uploads")
MAX_SIDE = 2000
NO_TEXT = "[no text]"
MATCH = 0.8

PROMPT = (
    "Transcribe faithfully the handwritten text in this image, in the language it is written in. "
    "Keep line breaks and lists (simple Markdown). Do not summarise, correct, translate or comment. "
    "If a word cannot be read write [illegible]; if unsure, put your guess in [? ]. "
    f"If the image contains no writing at all, answer exactly {NO_TEXT}."
)


class ReadError(RuntimeError):
    pass


class Settings:
    def __init__(self):
        self.ollama_host = os.environ.get("OLLAMA_HOST", "http://ollama:11434").rstrip("/")
        self.model = os.environ.get("VISION_MODEL", "qwen3-vl:8b-instruct")
        self.keep_alive = f"{int(os.environ.get('IDLE_UNLOAD_MINUTES', '10'))}m"


app = FastAPI(title="local-ai-core reader worker", version=VERSION)
app.state.settings = Settings()
app.state.transport = None  # tests put a fake Ollama here


def client(timeout):
    return httpx.AsyncClient(base_url=app.state.settings.ollama_host, transport=app.state.transport, timeout=timeout)


def words(text):
    return re.findall(r"\w+", unicodedata.normalize("NFKC", text).lower())


def score(heard, expected):
    a, b = words(heard), words(expected)
    return 1.0 if not a and not b else round(difflib.SequenceMatcher(None, a, b).ratio(), 3)


def prepare(path):
    """Straightened by EXIF, RGB, at most MAX_SIDE pixels a side, JPEG: what the model reads best."""
    from PIL import Image, ImageOps, UnidentifiedImageError
    try:
        img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    except (UnidentifiedImageError, OSError) as e:
        raise ReadError(f"not an image: {e}") from None
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    return base64.b64encode(buf.getvalue()).decode()


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/info")
async def info():
    m = app.state.settings.model
    return {
        "worker": "reader", "version": VERSION, "profile": "handwriting",
        "services": [{
            "name": "handwriting", "title": "Handwriting",
            "description": "Photos of handwritten notes become text, with a vision model on Ollama; the picture "
                           "never leaves the machine. Downloads its model on first use.",
            "model": m, "vram_gb": 7, "license": "Apache-2.0", "pulls_model_on_first_use": True,
            "license_url": "https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct", "endpoints": ["/v1/handwriting"],
        }],
    }


async def ensure_model():
    """Async generator: pulls the vision model if Ollama lacks it, with progress on the whole model."""
    m = app.state.settings.model
    async with client(10) as c:
        tags = [t["name"] for t in (await c.get("/api/tags")).json().get("models", [])]
    if m in tags or (":" not in m and m + ":latest" in tags):
        return
    layers = {}
    async with client(httpx.Timeout(None, connect=10)) as c:
        async with c.stream("POST", "/api/pull", json={"model": m, "stream": True}) as r:
            async for line in r.aiter_lines():
                if not line.strip():
                    continue
                d = json.loads(line)
                if "error" in d:
                    raise ReadError(f"pull of {m} failed: {d['error']}")
                if d.get("digest") and d.get("total"):
                    layers[d["digest"]] = (d.get("completed") or 0, d["total"])
                    yield {"type": "progress", "message": f"downloading {m}",
                           "done": sum(x for x, _ in layers.values()), "total": sum(t for _, t in layers.values())}


async def read(path, expect=None):
    """Async generator: progress, then {"type": "result", "text", "empty", "score"?}."""
    s = app.state.settings
    image = prepare(path)
    try:
        async for ev in ensure_model():
            yield ev
        yield {"type": "progress", "message": "reading"}
        t0, pieces, end = time.time(), [], {}
        body = {"model": s.model, "stream": True, "think": False, "keep_alive": s.keep_alive,
                "messages": [{"role": "user", "content": PROMPT, "images": [image]}],
                "options": {"temperature": 0, "num_ctx": 8192, "num_predict": 3000, "repeat_penalty": 1.1}}
        async with client(httpx.Timeout(600, connect=10)) as c:
            async with c.stream("POST", "/api/chat", json=body) as r:
                if r.status_code != 200:
                    raise ReadError(f"Ollama answered {r.status_code}: {(await r.aread())[:200]!r}")
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    d = json.loads(line)
                    if "error" in d:
                        raise ReadError(f"Ollama says: {d['error'][:200]}")
                    pieces.append(d.get("message", {}).get("content", ""))
                    if d.get("done"):
                        end = d
    except httpx.HTTPError as e:
        raise ReadError(f"Ollama not reachable at {s.ollama_host}: {e}") from None
    text = "".join(pieces).strip()
    if not end:
        raise ReadError("connection closed before the end of the answer")
    if end.get("done_reason") == "length":
        text += "\n\n[cut: the answer reached its length limit]"
    empty = text == NO_TEXT or not words(text.replace(NO_TEXT, ""))
    res = {"type": "result", "text": "" if empty else text, "empty": empty, "model": s.model,
           "secs": round(time.time() - t0, 1)}
    if expect is not None:
        res["score"] = score(res["text"], expect)
        res["matches"] = res["score"] >= MATCH
    yield res


def ndjson(gen):
    async def body():
        try:
            async for ev in gen:
                yield json.dumps(ev, ensure_ascii=False) + "\n"
        except ReadError as e:
            yield json.dumps({"type": "error", "message": str(e)}) + "\n"
    return StreamingResponse(body(), media_type="application/x-ndjson")


def inside_uploads(p):
    real, root = os.path.realpath(p), os.path.realpath(UPLOADS)
    return real == root or real.startswith(root + os.sep)


class ReadReq(BaseModel):
    path: str
    expect: str | None = None


@app.post("/read")
async def read_endpoint(req: ReadReq):
    if not inside_uploads(req.path):
        raise HTTPException(400, "the path must be inside the uploads volume")
    if not os.path.isfile(req.path):
        raise HTTPException(404, "no such file")
    return ndjson(read(req.path, req.expect))


# ------------------------------------------------------------------------------------------ self-test

async def selftest_checks():
    """selftest/note.jpg: an invented note in a handwriting font (scripts/make-handwriting-fixtures.sh),
    its text in note.txt. Introduced errors: a blank page must not be "read", a different expected text must
    not match."""
    def check(name, ok, detail):
        return {"type": "check", "name": name, "ok": bool(ok), "detail": detail}

    expected = open(os.path.join(HERE, "selftest", "note.txt"), encoding="utf-8").read()
    res = None
    try:
        async for ev in read(os.path.join(HERE, "selftest", "note.jpg"), expected):
            if ev["type"] == "result":
                res = ev
            else:
                yield ev
        yield check("handwritten note read", res["matches"],
                    f"similarity {res['score']:.2f} (≥ {MATCH}): «{' '.join(res['text'].split())[:90]}»")
        other = "The weather in the mountains will be cold and windy all week long."
        s = score(res["text"], other)
        yield check("introduced error: a different expected text", s < MATCH, f"similarity {s:.2f}: not matching")
    except ReadError as e:
        yield check("handwritten note read", False, str(e))
        return
    from PIL import Image
    with tempfile.NamedTemporaryFile(suffix=".png") as f:
        Image.new("RGB", (1400, 900), (250, 248, 240)).save(f.name)
        try:
            async for ev in read(f.name, expected):
                if ev["type"] == "result":
                    res = ev
            yield check("introduced error: a blank page", res["empty"] and res["score"] < 0.2,
                        "nothing invented" if res["empty"] else f"it «read»: {res['text'][:80]}")
        except ReadError as e:
            yield check("introduced error: a blank page", False, str(e))


@app.post("/selftest")
async def selftest():
    async def body():
        ok = True
        try:
            async for c in selftest_checks():
                if c["type"] == "check":
                    ok = ok and c["ok"]
                yield json.dumps(c, ensure_ascii=False) + "\n"
        except ReadError as e:
            ok = False
            yield json.dumps({"type": "check", "name": "self-test", "ok": False, "detail": str(e)}) + "\n"
        yield json.dumps({"type": "result", "ok": ok}) + "\n"
    return StreamingResponse(body(), media_type="application/x-ndjson")
