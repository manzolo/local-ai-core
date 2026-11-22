"""Voice worker: text → speech with XTTS-v2, long texts in short pieces, each piece listened to again.

XTTS-v2 is under the Coqui Public Model License: NON-COMMERCIAL use only, of the model and of what it
produces, and it must be accepted. This worker does not accept it for you: it downloads and runs the model
only when XTTS_ACCEPT_CPML=yes is set. Only the model's built-in voices are offered (no cloning).

Internal API, on the compose network only:
  GET  /health      liveness
  GET  /info        the service, its model, license, whether the license was accepted
  GET  /voices      the built-in voices
  POST /speak       {"text", "voice", "language", "format", "verify", "out"} → NDJSON progress, result
  POST /unload      frees the model (the container restarts to give back all its VRAM)
  POST /selftest    known text → expected audio, plus introduced errors (NDJSON)
"""
import asyncio
import contextlib
import difflib
import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import wave

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import text as textmod

VERSION = "0.1.0"
HERE = os.path.dirname(os.path.abspath(__file__))
UPLOADS = os.environ.get("UPLOADS_DIR", "/data/uploads")
IDLE_MINUTES = int(os.environ.get("IDLE_UNLOAD_MINUTES", "10"))
TRANSCRIBER = os.environ.get("TRANSCRIBER_URL", "http://transcriber:8000").rstrip("/")
DEFAULT_VOICE = "Claribel Dervla"
LANGUAGES = ["en", "it", "es", "fr", "de", "pt", "pl", "tr", "ru", "nl", "cs", "ar", "zh-cn", "ja", "hu", "ko", "hi"]
MATCH = 0.85
RETRIES = 3
LICENSE_URL = "https://huggingface.co/coqui/XTTS-v2/blob/main/LICENSE.txt"  # coqui.ai/cpml.txt is gone with Coqui
# The license asks that whoever gets the model's output from you also gets its terms or their address: the
# notice is written inside every audio file (WAV LIST/INFO comment, MP3 and OGG comment tag).
NOTICE = ("Made with XTTS-v2. Coqui Public Model License 1.0.0: non-commercial use only, of the model and "
          f"of its output. Terms: {LICENSE_URL}")


def accepted():
    return os.environ.get("XTTS_ACCEPT_CPML", "").strip().lower() in ("yes", "true", "1")


class SpeakError(RuntimeError):
    pass


class XTTS:
    """XTTS-v2, loaded lazily. Importing this module needs no coqui-tts (tests run on CPU without it)."""

    def __init__(self):
        self.tts = None
        self.device = None
        self._lock = threading.Lock()
        self.sample_rate = 24000

    @property
    def loaded(self):
        return self.tts is not None

    def load(self):
        with self._lock:
            if self.tts is None:
                if not accepted():
                    raise SpeakError("XTTS-v2 is under the Coqui Public Model License: non-commercial use only. "
                                     "To accept it, set XTTS_ACCEPT_CPML=yes in .env and restart the voice service")
                os.environ["COQUI_TOS_AGREED"] = "1"  # accepted by the user, above: coqui-tts asks for it
                import torch
                from TTS.api import TTS
                self.device = "cuda" if torch.cuda.is_available() else "cpu"
                self.tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(self.device)
                self.sample_rate = self.tts.synthesizer.output_sample_rate
            return self.tts

    def voices(self):
        return sorted(self.load().speakers or [])

    def speak(self, piece, language, voice):
        import numpy as np
        wav = self.load().tts(piece, language=language, speaker=voice, split_sentences=False)
        return np.asarray(wav, dtype=np.float32)

    def unload(self):
        with self._lock:
            if self.tts is None:
                return False
            self.tts = None
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
            if IDLE_MINUTES and time.time() - app.state.last_used > IDLE_MINUTES * 60 and not app.state.lock.locked():
                if app.state.engine.unload():
                    restart_soon()
    task = asyncio.create_task(idle_unloader())
    yield
    task.cancel()


app = FastAPI(title="local-ai-core voice worker", version=VERSION, lifespan=lifespan)
app.state.engine = XTTS()
app.state.lock = asyncio.Lock()
app.state.last_used = time.time()
app.state.transcriber_transport = None  # tests put a fake transcriber here


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/info")
async def info():
    return {
        "worker": "voice", "version": VERSION, "profile": "voice",
        "services": [{
            "name": "voice", "title": "Voice",
            "description": "Text becomes speech with XTTS-v2 and its built-in voices: long texts in short pieces, "
                           "numbers spelled out, each piece listened to again by the transcriber when it runs.",
            "model": "XTTS-v2 (coqui-tts 0.27.5)", "ollama": False, "vram_gb": 3,
            "license": "Coqui Public Model License: NON-COMMERCIAL use only",
            "license_url": LICENSE_URL, "accepted": accepted(),
            "use_restrictions": "non-commercial use only, of the model and of the audio it makes",
            "loaded": app.state.engine.loaded, "endpoints": ["/v1/speak", "/v1/voices"],
        }],
    }


@app.get("/voices")
async def voices():
    try:
        async with app.state.lock:
            return {"voices": await asyncio.to_thread(app.state.engine.voices), "default": DEFAULT_VOICE}
    except SpeakError as e:
        raise HTTPException(403, str(e)) from None


def write_wav(path, samples, rate, notice=None):
    """16-bit mono WAV; with `notice`, a LIST/INFO chunk carries it as the file's comment (ICMT)."""
    import numpy as np
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    if notice:
        def sub(tag, text):
            data = text.encode("utf-8") + b"\0"
            data += b"\0" * (len(data) % 2)  # chunks are word-aligned
            return tag + struct.pack("<I", len(data)) + data
        info = b"INFO" + sub(b"ICMT", notice) + sub(b"ISFT", "local-ai-core voice (XTTS-v2)")
        with open(path, "r+b") as f:
            f.seek(0, 2)
            f.write(b"LIST" + struct.pack("<I", len(info)) + info)
            size = f.tell() - 8
            f.seek(4)
            f.write(struct.pack("<I", size))


def notice_of(path):
    """The license notice carried by an audio file we made, or None (WAV: ICMT; MP3/OGG: ffprobe)."""
    if path.endswith(".wav"):
        with open(path, "rb") as f:
            data = f.read()
        i = data.find(b"ICMT")
        if i < 0:
            return None
        n = struct.unpack("<I", data[i + 4:i + 8])[0]
        return data[i + 8:i + 8 + n].rstrip(b"\0").decode("utf-8", "replace")
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags:stream_tags", "-of", "json", path],
                       capture_output=True, text=True)
    tags = {}
    for part in json.loads(p.stdout or "{}").get("format", {}), *json.loads(p.stdout or "{}").get("streams", []):
        tags.update({k.lower(): v for k, v in part.get("tags", {}).items()})
    return tags.get("comment")


def heard_as_said(heard, language):
    """What Whisper wrote, in the form the voice was given: it writes numbers as digits ("since 6.30", "at 8")
    while the voice said them in words, so its digits are spelled out the same way (6.30 is a time)."""
    heard = re.sub(r"\b(\d{1,2})\.(\d{2})\b", r"\1:\2", heard)
    return textmod.spell(heard, language)


def similarity(heard, expected, language):
    a = re.findall(r"\w+", heard_as_said(heard, language).lower())
    b = re.findall(r"\w+", expected.lower())
    return 1.0 if not a and not b else round(difflib.SequenceMatcher(None, a, b).ratio(), 3)


async def listen(path, expected, language):
    """How closely the transcriber hears `expected` in the audio (0..1), or None when it is not running."""
    try:
        async with httpx.AsyncClient(base_url=TRANSCRIBER, transport=app.state.transcriber_transport,
                                     timeout=httpx.Timeout(300, connect=3)) as c:
            async with c.stream("POST", "/transcribe", json={"path": path, "language": language,
                                                             "expect": expected}) as r:
                if r.status_code != 200:
                    return None
                async for line in r.aiter_lines():
                    if line.strip():
                        ev = json.loads(line)
                        if ev.get("type") == "result":
                            return similarity(ev.get("text", ""), expected, language)
    except httpx.HTTPError:
        return None
    return None


async def speak(text, voice=DEFAULT_VOICE, language="en", fmt="wav", verify=True, out=None):
    """Async generator: progress, then the result; writes the audio to `out`."""
    import numpy as np
    parts = textmod.pieces(text, language)
    if not parts:
        raise SpeakError("the text has no words to say")
    eng = app.state.engine
    work = tempfile.mkdtemp(prefix="voice-", dir=UPLOADS if os.path.isdir(UPLOADS) else None)
    async with app.state.lock:
        t0 = time.time()
        try:
            if not eng.loaded:
                yield {"type": "progress", "message": "loading XTTS-v2 (the first time it downloads ~1.9 GB)"}
            await asyncio.to_thread(eng.load)
            if voice not in await asyncio.to_thread(eng.voices):
                raise SpeakError(f"unknown voice {voice!r}: GET /v1/voices lists them")
            audio, scores, retried, checked = [], [], 0, None
            for i, (piece, pause) in enumerate(parts, 1):
                yield {"type": "progress", "message": "speaking", "done": i - 1, "total": len(parts)}
                best, best_score = None, -1.0
                for attempt in range(RETRIES if verify else 1):
                    wav = await asyncio.to_thread(eng.speak, piece, language, voice)
                    if not verify:
                        best = wav
                        break
                    tmp = os.path.join(work, f"p{i}-{attempt}.wav")
                    write_wav(tmp, wav, eng.sample_rate)
                    s = await listen(tmp, piece, language)
                    if s is None:  # no transcriber: nothing to compare with
                        checked, best = False, wav
                        break
                    checked = True
                    if s > best_score:
                        best, best_score = wav, s
                    if s >= MATCH:
                        break
                    retried += 1
                if checked:
                    scores.append((best_score, piece))
                audio.append(best)
                if pause:
                    audio.append(np.zeros(int(eng.sample_rate * pause), dtype=np.float32))
            samples = np.concatenate(audio)
            wav_path = os.path.join(work, "all.wav")
            write_wav(wav_path, samples, eng.sample_rate, notice=NOTICE)
            if fmt == "wav":
                shutil.move(wav_path, out)
            else:
                codec = {"mp3": ["-c:a", "libmp3lame", "-q:a", "3"], "ogg": ["-c:a", "libopus", "-b:a", "64k"]}[fmt]
                p = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", wav_path, *codec,
                                    "-metadata", f"comment={NOTICE}", out], capture_output=True)
                if p.returncode != 0:
                    raise SpeakError(f"encoding to {fmt} failed: {p.stderr.decode(errors='replace')[:200]}")
        finally:
            app.state.last_used = time.time()
            shutil.rmtree(work, ignore_errors=True)
    below = [p for s, p in scores if s < MATCH]
    yield {"type": "result", "duration": round(len(samples) / eng.sample_rate, 2), "pieces": len(parts),
           "voice": voice, "language": language, "format": fmt, "secs": round(time.time() - t0, 1),
           "verify": {"checked": bool(checked), "min_score": round(min(s for s, _ in scores), 3) if scores else None,
                      "retried": retried, "below": below,
                      "note": None if checked or not verify else "the transcriber is not running: not listened to"}}


def ndjson(gen):
    async def body():
        try:
            async for ev in gen:
                yield json.dumps(ev, ensure_ascii=False) + "\n"
        except SpeakError as e:
            yield json.dumps({"type": "error", "message": str(e)}) + "\n"
    return StreamingResponse(body(), media_type="application/x-ndjson")


def inside_uploads(p):
    real, root = os.path.realpath(p), os.path.realpath(UPLOADS)
    return real == root or real.startswith(root + os.sep)


class SpeakReq(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    voice: str = DEFAULT_VOICE
    language: str = "en"
    format: str = Field("wav", pattern="^(wav|mp3|ogg)$")
    verify: bool = True
    out: str


@app.post("/speak")
async def speak_endpoint(r: SpeakReq):
    if not inside_uploads(r.out):
        raise HTTPException(400, "the output must be inside the uploads volume")
    if r.language not in LANGUAGES:
        raise HTTPException(422, f"language must be one of {', '.join(LANGUAGES)}")
    if not accepted():
        raise HTTPException(403, "XTTS-v2 is under the Coqui Public Model License: non-commercial use only. "
                                 "To accept it, set XTTS_ACCEPT_CPML=yes in .env and restart the voice service")
    return ndjson(speak(r.text, r.voice, r.language, r.format, r.verify, r.out))


@app.post("/unload")
async def unload():
    async with app.state.lock:
        unloaded = app.state.engine.unload()
    if unloaded:
        restart_soon()
    return {"unloaded": unloaded}


# ------------------------------------------------------------------------------------------ self-test

PHRASE = "Seven blue boxes arrived at the station on Tuesday morning, and nobody opened them."


async def selftest_checks():
    """A known sentence → a 24 kHz WAV of plausible length and loudness, listened to again by the transcriber
    when it runs; numbers spelled out; introduced errors: a text with no words is refused, a different
    expected text does not match."""
    def check(name, ok, detail):
        return {"type": "check", "name": name, "ok": bool(ok), "detail": detail}

    if not accepted():
        yield check("license accepted", False, "XTTS_ACCEPT_CPML is not set to yes: the voice service is off")
        return
    try:
        names = await asyncio.to_thread(app.state.engine.voices)
        yield check("built-in voices", len(names) >= 50 and DEFAULT_VOICE in names, f"{len(names)} voices")
    except SpeakError as e:
        yield check("built-in voices", False, str(e))
        return
    got = " ".join(p for p, _ in textmod.pieces("Meet me at 10:30 on 3 May 2027.", "en"))
    want = "Meet me at ten thirty on three May twenty twenty-seven"
    yield check("numbers spelled out", got == want, f"«{got}»")

    tmp = tempfile.mkdtemp(prefix="selftest-", dir=UPLOADS if os.path.isdir(UPLOADS) else None)
    try:
        out = os.path.join(tmp, "phrase.wav")
        res = None
        try:
            async for ev in speak(PHRASE, DEFAULT_VOICE, "en", "wav", True, out):
                if ev["type"] == "result":
                    res = ev
                else:
                    yield ev
        except SpeakError as e:
            yield check("known sentence spoken", False, str(e))
            return
        import numpy as np
        with wave.open(out) as w:
            rate, frames = w.getframerate(), w.readframes(w.getnframes())
        samples = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32767
        loud = float(np.sqrt(np.mean(samples ** 2)))
        plausible = 0.03 * len(PHRASE) <= res["duration"] <= 0.15 * len(PHRASE)
        yield check("known sentence spoken", rate == 24000 and plausible and loud > 0.01,
                    f"{res['duration']} s at {rate} Hz, loudness {loud:.3f}")
        carried = notice_of(out) or ""
        yield check("the license notice travels inside the file",
                    "non-commercial" in carried and LICENSE_URL in carried, carried[:90] or "no notice in the file")
        v = res["verify"]
        if v["checked"]:
            yield check("listened to again by the transcriber", v["min_score"] >= MATCH,
                        f"similarity {v['min_score']:.2f}, {v['retried']} piece(s) redone")
            s = textmod_score(out, "Tomorrow it will rain on the hills and nobody will leave the house.")
            score = await s
            yield check("introduced error: a different expected text", score is not None and score < MATCH,
                        f"similarity {score:.2f}: not matching" if score is not None else "no answer")
        else:
            yield check("listened to again by the transcriber", True,
                        "skipped: the transcriber is not running (profile transcribe)")
        try:
            async for _ in speak("… «» !", DEFAULT_VOICE, "en", "wav", False, os.path.join(tmp, "x.wav")):
                pass
            yield check("introduced error: a text with no words", False, "it was spoken")
        except SpeakError as e:
            yield check("introduced error: a text with no words", "no words" in str(e), f"refused: {e}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def textmod_score(path, expected):
    return await listen(path, expected, "en")


@app.post("/selftest")
async def selftest():
    async def body():
        ok = True
        async for c in selftest_checks():
            if c["type"] == "check":
                ok = ok and c["ok"]
            yield json.dumps(c, ensure_ascii=False) + "\n"
        yield json.dumps({"type": "result", "ok": ok}) + "\n"
    return StreamingResponse(body(), media_type="application/x-ndjson")
