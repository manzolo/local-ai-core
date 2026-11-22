"""Speakers worker: who said what. Each transcribed sentence gets a voice fingerprint (SpeechBrain ECAPA,
trained on VoxCeleb, no token needed); the fingerprints are grouped into speakers.

Internal API, on the compose network only:
  GET  /health      liveness
  GET  /info        the service, its model, VRAM estimate, license
  POST /attribute   {"path", "segments": [[start, end, text]], "n": 0, "threshold": 0.55}
                    → {"speakers": K, "labels": [1, 2, 1, ...]} (speakers numbered in order of first speech)
  POST /unload      frees the model now
  POST /selftest    known dialogues → expected speakers, plus introduced errors (NDJSON)

How: for every sentence of at least 0.6 s, an ECAPA embedding over 3 s windows every 1.5 s, averaged. With
n, exactly n speakers (agglomerative clustering, cosine distance, average linkage); without, two sentences
join when their cosine distance is under `threshold`. Shorter sentences take the speaker of the nearest
sentence in time.
"""
import asyncio
import contextlib
import json
import os
import subprocess
import threading
import time

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

VERSION = "0.1.0"
HERE = os.path.dirname(os.path.abspath(__file__))
UPLOADS = os.environ.get("UPLOADS_DIR", "/data/uploads")
MODELS = os.environ.get("MODELS_DIR", "/models")
IDLE_MINUTES = int(os.environ.get("IDLE_UNLOAD_MINUTES", "10"))
SR = 16000
MIN_SEG = 0.6
WIN, HOP = 3.0, 1.5


class SpeakersError(RuntimeError):
    pass


def decode(path):
    import numpy as np
    proc = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(SR),
                           "-f", "f32le", "-"], capture_output=True)
    if proc.returncode != 0:
        raise SpeakersError(f"cannot decode the audio: {proc.stderr.decode(errors='replace')[:200]}")
    return np.frombuffer(proc.stdout, dtype=np.float32)


def cluster(X, n, threshold):
    """Group the embeddings: labels 0..K-1 (pure numpy/sklearn, no model needed: tested on CPU)."""
    import numpy as np
    if len(X) == 1 or n == 1:
        return np.zeros(len(X), dtype=int)
    from sklearn.cluster import AgglomerativeClustering
    if n:
        return AgglomerativeClustering(n_clusters=min(n, len(X)), metric="cosine", linkage="average").fit_predict(X)
    return AgglomerativeClustering(n_clusters=None, metric="cosine", linkage="average",
                                   distance_threshold=threshold).fit_predict(X)


def assign(segs, idx, labels):
    """Labels for every sentence: the measured ones, then the short ones from the nearest measured sentence;
    speakers renumbered 1.. in order of first speech."""
    lab = [None] * len(segs)
    for i, label in zip(idx, labels, strict=True):
        lab[i] = int(label)
    mids = [(s + e) / 2 for s, e, _ in segs]
    for i in range(len(segs)):
        if lab[i] is None:
            j = min(idx, key=lambda k: abs(mids[k] - mids[i]))
            lab[i] = int(labels[idx.index(j)])
    order = {}
    for label in lab:
        order.setdefault(label, len(order) + 1)
    return len(order), [order[label] for label in lab]


class Encoder:
    def __init__(self):
        self.enc = None
        self.device = None
        self._lock = threading.Lock()

    @property
    def loaded(self):
        return self.enc is not None

    def load(self):
        with self._lock:
            if self.enc is None:
                import torch
                from speechbrain.inference.speaker import EncoderClassifier
                self.device = "cuda" if torch.cuda.is_available() else "cpu"
                self.enc = EncoderClassifier.from_hparams(
                    source="speechbrain/spkrec-ecapa-voxceleb", savedir=os.path.join(MODELS, "spkrec-ecapa-voxceleb"),
                    run_opts={"device": self.device})
            return self.enc

    def embed(self, wav, start, end):
        import numpy as np
        import torch
        enc = self.load()
        chunks, t = [], start
        while True:
            b = min(t + WIN, end)
            chunks.append(wav[int(t * SR):int(b * SR)])
            if b >= end:
                break
            t += HOP
        n = max(len(c) for c in chunks)
        batch = np.zeros((len(chunks), n), dtype=np.float32)
        lens = np.zeros(len(chunks), dtype=np.float32)
        for i, c in enumerate(chunks):
            batch[i, :len(c)] = c
            lens[i] = len(c) / n
        with torch.no_grad():
            e = enc.encode_batch(torch.from_numpy(batch).to(self.device), torch.from_numpy(lens).to(self.device))
        e = e.squeeze(1).cpu().numpy()
        e /= np.linalg.norm(e, axis=1, keepdims=True) + 1e-9
        m = e.mean(axis=0)
        return m / (np.linalg.norm(m) + 1e-9)

    def unload(self):
        with self._lock:
            if self.enc is None:
                return False
            self.enc = None
        try:
            import gc

            import torch
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass
        return True


def attribute(engine, path, segs, n=0, threshold=0.55):
    import numpy as np
    if not segs:
        raise SpeakersError("no sentences to attribute")
    wav = decode(path)
    total = len(wav) / SR
    idx, embs = [], []
    for i, (st, en, _) in enumerate(segs):
        if en - st >= MIN_SEG and st < total:
            idx.append(i)
            embs.append(engine.embed(wav, st, min(en, total)))
    if not embs:
        raise SpeakersError("no sentence long enough to recognise a voice")
    k, labels = assign(segs, idx, cluster(np.stack(embs), n, threshold))
    return {"speakers": k, "labels": labels}


@contextlib.asynccontextmanager
async def lifespan(app):
    async def idle_unloader():
        while True:
            await asyncio.sleep(30)
            if IDLE_MINUTES and time.time() - app.state.last_used > IDLE_MINUTES * 60 and not app.state.lock.locked():
                app.state.engine.unload()
    task = asyncio.create_task(idle_unloader())
    yield
    task.cancel()


app = FastAPI(title="local-ai-core speakers worker", version=VERSION, lifespan=lifespan)
app.state.engine = Encoder()
app.state.last_used = time.time()
app.state.lock = asyncio.Lock()


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/info")
async def info():
    return {
        "worker": "speakers", "version": VERSION, "profile": "speakers",
        "services": [{
            "name": "speakers", "title": "Who said what",
            "description": "Splits a transcript by voice: each sentence gets its speaker (SpeechBrain ECAPA, "
                           "no account or token needed). Used by /v1/transcribe with speakers=true.",
            "model": "SpeechBrain 1.1.2 · spkrec-ecapa-voxceleb", "ollama": False, "vram_gb": 1,
            "license": "Apache-2.0", "license_url": "https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb",
            "loaded": app.state.engine.loaded, "endpoints": ["/v1/transcribe?speakers=true"],
        }],
    }


class AttributeReq(BaseModel):
    path: str
    segments: list[tuple[float, float, str]]
    n: int = Field(0, ge=0, le=20, description="exact number of speakers, 0 = find out")
    threshold: float = Field(0.55, gt=0, lt=2, description="cosine distance under which two sentences join")


def inside_uploads(p):
    real, root = os.path.realpath(p), os.path.realpath(UPLOADS)
    return real == root or real.startswith(root + os.sep)


async def run_attribute(path, segs, n, threshold):
    async with app.state.lock:
        try:
            return await asyncio.to_thread(attribute, app.state.engine, path, segs, n, threshold)
        finally:
            app.state.last_used = time.time()


@app.post("/attribute")
async def attribute_endpoint(req: AttributeReq):
    if not inside_uploads(req.path):
        raise HTTPException(400, "the path must be inside the uploads volume")
    if not os.path.isfile(req.path):
        raise HTTPException(404, "no such file")
    try:
        return await run_attribute(req.path, [list(s) for s in req.segments], req.n, req.threshold)
    except SpeakersError as e:
        raise HTTPException(422, str(e)) from None


@app.post("/unload")
async def unload():
    async with app.state.lock:
        return {"unloaded": app.state.engine.unload()}


# ------------------------------------------------------------------------------------------ self-test

async def selftest_checks():
    """selftest/dialogue.ogg: six sentences, two public-domain synthetic voices alternating A B A B A B;
    selftest/one-voice.ogg: three sentences of one voice. The introduced error: one voice must not become
    two speakers (a clustering that always splits, or a threshold off the scale, is caught here)."""
    def check(name, ok, detail):
        return {"type": "check", "name": name, "ok": bool(ok), "detail": detail}

    fx = os.path.join(HERE, "selftest")

    def load(name):
        with open(os.path.join(fx, name + ".json")) as f:
            return os.path.join(fx, name + ".ogg"), json.load(f)

    path, segs = load("dialogue")
    try:
        r = await run_attribute(path, segs, 0, 0.55)
        seq = "".join(map(str, r["labels"]))
        yield check("dialogue: two speakers found unaided", r["speakers"] == 2 and seq == "121212",
                    f"{r['speakers']} speakers, sequence {seq} (expected 121212) on {app.state.engine.device}")
        r = await run_attribute(path, segs, 2, 0.55)
        seq = "".join(map(str, r["labels"]))
        yield check("dialogue with n=2", seq == "121212", f"sequence {seq}")
        path1, segs1 = load("one-voice")
        r = await run_attribute(path1, segs1, 0, 0.55)
        seq = "".join(map(str, r["labels"]))
        yield check("introduced error: one voice must stay one speaker", r["speakers"] == 1,
                    f"{r['speakers']} speaker(s), sequence {seq}")
    except SpeakersError as e:
        yield check("dialogue: two speakers found unaided", False, str(e))
    try:
        await run_attribute(path, [], 0, 0.55)
        yield check("introduced error: no sentences", False, "an empty transcript was attributed")
    except SpeakersError as e:
        yield check("introduced error: no sentences", True, f"refused: {e}")


@app.post("/selftest")
async def selftest():
    async def body():
        ok = True
        async for c in selftest_checks():
            ok = ok and c["ok"]
            yield json.dumps(c, ensure_ascii=False) + "\n"
        yield json.dumps({"type": "result", "ok": ok}) + "\n"
    return StreamingResponse(body(), media_type="application/x-ndjson")
