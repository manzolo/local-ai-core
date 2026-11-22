"""Transcriber worker: speech → text with faster-whisper (large-v3), audio and video alike.

Internal API, on the compose network only:
  GET  /health      liveness
  GET  /info        the service, its model, VRAM estimate, license, whether the model is loaded
  POST /transcribe  {"path", "language", "expect"?} → NDJSON: progress (seconds done of total), then the
                    result: language, duration, segments [[start, end, text]], text, score (if expect)
  POST /unload      frees the model (and its VRAM) now
  POST /selftest    known speech → expected text, plus introduced errors (NDJSON)

Audio is decoded with ffmpeg, not with faster-whisper's own decoder: faster-whisper 1.2.1 with PyAV 19
fails to open files (open() got an unexpected keyword argument 'metadata_errors').
The model loads on first use and unloads after IDLE_UNLOAD_MINUTES without work.
"""
import asyncio
import contextlib
import difflib
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import unicodedata

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

VERSION = "0.1.0"
HERE = os.path.dirname(os.path.abspath(__file__))
UPLOADS = os.environ.get("UPLOADS_DIR", "/data/uploads")
MODEL = os.environ.get("WHISPER_MODEL", "large-v3")
IDLE_MINUTES = int(os.environ.get("IDLE_UNLOAD_MINUTES", "10"))
SR = 16000
MATCH = 0.85  # how similar a transcript must be to the expected text to count as "says it"


class TranscribeError(RuntimeError):
    pass


def words(text):
    text = unicodedata.normalize("NFKC", text).lower().replace("’", "'")
    return re.findall(r"\w+", text)


def score(heard, expected):
    """Word-level similarity 0..1; case and punctuation do not count."""
    a, b = words(heard), words(expected)
    if not a and not b:
        return 1.0
    return round(difflib.SequenceMatcher(None, a, b).ratio(), 3)


def decode(path):
    """Audio or video → float32 mono samples at 16 kHz."""
    import numpy as np
    proc = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", path, "-vn", "-f", "f32le",
                           "-ac", "1", "-ar", str(SR), "-"], capture_output=True)
    if proc.returncode != 0:
        lines = proc.stderr.decode(errors="replace").strip().splitlines()
        raise TranscribeError(f"cannot decode the audio: {lines[-1] if lines else 'ffmpeg failed'}")
    audio = np.frombuffer(proc.stdout, dtype=np.float32)
    if not len(audio):
        raise TranscribeError("cannot decode the audio: no audio stream")
    return audio


class Whisper:
    """faster-whisper, loaded lazily (GPU in float16, else CPU in int8)."""

    def __init__(self):
        self.model = None
        self.device = None
        self._lock = threading.Lock()

    @property
    def loaded(self):
        return self.model is not None

    def load(self):
        with self._lock:
            if self.model is None:
                from faster_whisper import WhisperModel
                try:
                    self.model, self.device = WhisperModel(MODEL, device="cuda", compute_type="float16"), "cuda"
                except Exception:  # noqa: BLE001 — no GPU or no CUDA libraries: slower, same result
                    self.model, self.device = WhisperModel(MODEL, device="cpu", compute_type="int8"), "cpu"
            return self.model

    def transcribe(self, audio, language, on_segment):
        """Calls on_segment(start, end, text, duration) as segments come; returns (language, duration)."""
        segments, info = self.load().transcribe(audio, language=None if language == "auto" else language,
                                                beam_size=5, vad_filter=False)
        for s in segments:
            if s.text.strip():
                on_segment(s.start, s.end, s.text.strip(), info.duration)
        return info.language, info.duration

    def unload(self):
        with self._lock:
            if self.model is None:
                return False
            self.model = None
        import gc
        gc.collect()
        return True


def restart_soon():
    """The only way to give back all the VRAM, CUDA context and library caches included: leave. Docker
    restarts the container (restart: unless-stopped) clean, with no GPU memory, in a few seconds."""
    if os.environ.get("RESTART_TO_FREE_VRAM", "1") == "1":
        asyncio.get_running_loop().call_later(0.5, os._exit, 0)


@contextlib.asynccontextmanager
async def lifespan(app):
    async def idle_unloader():
        while True:
            await asyncio.sleep(30)
            if IDLE_MINUTES and time.time() - app.state.last_used > IDLE_MINUTES * 60 and not app.state.busy:
                if app.state.engine.unload():
                    restart_soon()
    task = asyncio.create_task(idle_unloader())
    yield
    task.cancel()


app = FastAPI(title="local-ai-core transcriber worker", version=VERSION, lifespan=lifespan)
app.state.engine = Whisper()
app.state.last_used = time.time()
app.state.busy = False
app.state.lock = asyncio.Lock()


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/info")
async def info():
    return {
        "worker": "transcriber", "version": VERSION, "profile": "transcribe",
        "services": [{
            "name": "transcribe", "title": "Speech to text",
            "description": "Audio and video become text and subtitles (SRT, WebVTT) with faster-whisper; "
                           "optionally checked against the text the speech should say.",
            "model": f"faster-whisper 1.2.1 · {MODEL}", "ollama": False, "vram_gb": 5, "license": "MIT",
            "license_url": "https://huggingface.co/Systran/faster-whisper-large-v3",
            "loaded": app.state.engine.loaded, "endpoints": ["/v1/transcribe"],
        }],
    }


def inside_uploads(p):
    real, root = os.path.realpath(p), os.path.realpath(UPLOADS)
    return real == root or real.startswith(root + os.sep)


async def run_transcribe(path, language="auto", expect=None):
    """Async generator: progress events, then the result event."""
    q: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    segs = []

    def on_segment(start, end, text, duration):
        segs.append([round(start, 2), round(end, 2), text])
        loop.call_soon_threadsafe(q.put_nowait, {"type": "progress", "done": round(end, 1),
                                                 "total": round(duration, 1)})

    def work():
        return app.state.engine.transcribe(decode(path), language, on_segment)

    async with app.state.lock:
        app.state.busy = True
        try:
            task = asyncio.create_task(asyncio.to_thread(work))
            while not task.done() or not q.empty():
                try:
                    yield await asyncio.wait_for(q.get(), 0.25)
                except TimeoutError:
                    pass
            lang, duration = task.result()
        finally:
            app.state.busy = False
            app.state.last_used = time.time()
    text = " ".join(t for _, _, t in segs).strip()
    result = {"type": "result", "language": lang, "duration": round(duration, 2), "segments": segs, "text": text,
              "device": app.state.engine.device}
    if expect is not None:
        result["score"] = score(text, expect)
        result["matches"] = result["score"] >= MATCH
    yield result


def ndjson(gen):
    async def body():
        try:
            async for ev in gen:
                yield json.dumps(ev, ensure_ascii=False) + "\n"
        except TranscribeError as e:
            yield json.dumps({"type": "error", "message": str(e)}) + "\n"
    return StreamingResponse(body(), media_type="application/x-ndjson")


class TranscribeReq(BaseModel):
    path: str
    language: str = "auto"
    expect: str | None = None


@app.post("/transcribe")
async def transcribe(req: TranscribeReq):
    if not inside_uploads(req.path):
        raise HTTPException(400, "the path must be inside the uploads volume")
    if not os.path.isfile(req.path):
        raise HTTPException(404, "no such file")
    return ndjson(run_transcribe(req.path, req.language, req.expect))


@app.post("/unload")
async def unload():
    async with app.state.lock:
        unloaded = app.state.engine.unload()
    if unloaded:
        restart_soon()
    return {"unloaded": unloaded}


# ------------------------------------------------------------------------------------------ self-test

PHRASE = "Seven blue boxes arrived at the station on Tuesday morning, and nobody opened them."
WRONG = "Tomorrow it will rain on the hills and nobody will leave the house."


async def selftest_checks():
    """selftest/phrase.ogg (a public-domain synthetic voice, scripts/make-audio-fixtures.sh) says PHRASE.
    Introduced errors: a different expected text must not "match", a file that is not audio is an error."""
    def check(name, ok, detail):
        return {"type": "check", "name": name, "ok": bool(ok), "detail": detail}

    res = None
    try:
        async for ev in run_transcribe(os.path.join(HERE, "selftest", "phrase.ogg"), "auto", PHRASE):
            if ev["type"] == "result":
                res = ev
    except TranscribeError as e:
        yield check("known phrase transcribed", False, str(e))
        return
    yield check("known phrase transcribed", res["matches"],
                f"similarity {res['score']:.2f} (≥ {MATCH}): «{res['text'][:90]}» on {res['device']}")
    yield check("language detected", res["language"] == "en", f"{res['language']} (expected en)")
    last = res["segments"][-1][1] if res["segments"] else 0
    yield check("segments carry times", last > 3, f"{len(res['segments'])} segment(s), the last ends at {last} s")
    s = score(res["text"], WRONG)
    yield check("introduced error: a different expected text", s < MATCH,
                f"similarity {s:.2f}: reported as not matching")
    with tempfile.NamedTemporaryFile(suffix=".mp3") as f:
        f.write(b"this is not audio")
        f.flush()
        try:
            async for _ in run_transcribe(f.name):
                pass
            yield check("introduced error: a file that is not audio", False, "it was 'transcribed'")
        except TranscribeError as e:
            yield check("introduced error: a file that is not audio", True, str(e)[:100])


@app.post("/selftest")
async def selftest():
    async def body():
        ok = True
        async for c in selftest_checks():
            ok = ok and c["ok"]
            yield json.dumps(c, ensure_ascii=False) + "\n"
        yield json.dumps({"type": "result", "ok": ok}) + "\n"
    return StreamingResponse(body(), media_type="application/x-ndjson")
