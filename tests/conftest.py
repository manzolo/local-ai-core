import os
import shutil

import pytest

os.environ.setdefault("LOCAL_AI_NO_APP", "1")  # the gateway module builds no app at import time
os.environ.setdefault("RESTART_TO_FREE_VRAM", "0")  # workers stay alive in the tests

from fake_ollama import FakeOllama  # noqa: E402
from search_worker import main as worker  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLES = os.path.join(ROOT, "examples", "notes")


@pytest.fixture
def fake():
    return FakeOllama()


@pytest.fixture
def notes(tmp_path):
    d = tmp_path / "notes"
    shutil.copytree(EXAMPLES, d)
    return d


@pytest.fixture
def search_worker(fake, notes, tmp_path):
    """The real search worker app, wired to the fake Ollama and a scratch notes folder."""
    s = worker.Settings()
    s.ollama_host = "http://fake-ollama"
    s.notes_dir = str(notes)
    s.index_dir = str(tmp_path / "index")
    s.library_dir = str(tmp_path / "library")
    s.min_score = 0.05  # bag-of-words vectors score lower than real embeddings
    worker.app.state.settings = s
    worker.app.state.transport = fake.transport
    return worker.app


class FakeConverter:
    """Stands in for Docling: a PDF whose bytes contain TEXT: lines "converts" to those lines; a scan
    (bytes contain SCAN) yields text only with OCR; anything else is a broken document."""

    def __init__(self):
        self.loaded = False
        self.calls = []

    def convert(self, src, ocr="auto", lang="en", tables=True):
        from documents_worker.main import ConversionError
        self.calls.append((src, ocr, lang, tables))
        self.loaded = True
        raw = open(src, "rb").read().decode("utf-8", "replace")
        if "SCAN" in raw:
            return ("" if ocr == "off" else "Energy 47.60\n"), 1, 1
        lines = [ln[5:] for ln in raw.splitlines() if ln.startswith("TEXT:")]
        if not lines:
            raise ConversionError("PdfiumError: Failed to load document")
        return "\n".join(lines) + "\n", 1, sum(1 for ln in lines if ln.startswith("|"))

    def unload(self):
        was, self.loaded = self.loaded, False
        return was


@pytest.fixture
def documents_worker(tmp_path, monkeypatch):
    from documents_worker import main as docs
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setattr(docs, "UPLOADS", str(uploads))
    docs.app.state.converter = FakeConverter()
    return docs.app


# ------------------------------------------------------------------ speech: fake Whisper and fake ECAPA
# A fake "audio" file is text: one line per sentence, "START END VOICE text…"; NOTAUDIO cannot be decoded.

class FakeAudio(list):
    """The sentences of a fake file; its length is the number of 16 kHz samples the audio would have."""

    def __len__(self):
        return int((self[-1][1] if list.__len__(self) else 0) * 16000) + 16000


def fake_decode_factory(error_cls):
    def decode(path):
        raw = open(path, encoding="utf-8", errors="replace").read()
        if "NOTAUDIO" in raw:
            raise error_cls("cannot decode the audio: Invalid data found when processing input")
        out = FakeAudio()
        for ln in raw.splitlines():
            parts = ln.split(" ", 3)
            try:
                if len(parts) == 4:
                    out.append((float(parts[0]), float(parts[1]), parts[2], parts[3]))
            except ValueError:  # a real (binary) audio file read as text: no sentences
                continue
        return out
    return decode


class FakeWhisper:
    loaded = False
    device = "cpu"

    def transcribe(self, audio, language, on_segment):
        self.loaded = True
        duration = audio[-1][1] if list.__len__(audio) else 0.0
        for start, end, _voice, text in audio:
            on_segment(start, end, text, duration)
        return ("en" if language == "auto" else language), duration

    def unload(self):
        self.loaded = False
        return True


class FakeEncoder:
    """A voice fingerprint per voice letter: A and B far apart, small differences per sentence."""
    loaded = False
    device = "cpu"

    def embed(self, wav, start, end):
        import numpy as np
        voice = next(v for s, e, v, _ in wav if abs(s - start) < 0.01)
        base = {"A": [1.0, 0.0, 0.0], "B": [0.0, 1.0, 0.0], "C": [0.0, 0.0, 1.0]}[voice]
        v = np.array(base) + 0.05 * np.array([start % 1, end % 1, 0.3])
        return v / np.linalg.norm(v)

    def unload(self):
        return False


@pytest.fixture
def speech_workers(tmp_path, monkeypatch):
    from speakers_worker import main as spk
    from transcriber_worker import main as tr
    uploads = tmp_path / "uploads"
    uploads.mkdir(exist_ok=True)
    for mod, err in ((tr, tr.TranscribeError), (spk, spk.SpeakersError)):
        monkeypatch.setattr(mod, "UPLOADS", str(uploads))
        monkeypatch.setattr(mod, "decode", fake_decode_factory(err))
    tr.app.state.engine = FakeWhisper()
    spk.app.state.engine = FakeEncoder()
    return tr.app, spk.app, uploads


# ------------------------------------------------------------------ images: fake ComfyUI and model server

@pytest.fixture
def image_worker(tmp_path, monkeypatch):
    """The real image worker, wired to a fake ComfyUI and a fake model server with small model files."""
    import hashlib

    import httpx
    from fake_comfy import FakeComfy
    from image_worker import main as img
    from image_worker import models

    uploads, root = tmp_path / "uploads", tmp_path / "models"
    uploads.mkdir(exist_ok=True)
    blobs, fakes = {}, {}
    for key in ("CHECKPOINT", "LIGHTNING", "REALESRGAN", "BIREFNET"):
        real = getattr(models, key)
        data = f"fake weights of {real.file}".encode() * 1000
        url = f"https://models.example/{real.file}"
        blobs[url] = data
        fakes[key] = models.Model(real.folder, real.file, url, hashlib.sha256(data).hexdigest(), len(data),
                                  real.license)
        monkeypatch.setattr(models, key, fakes[key])
    monkeypatch.setattr(models, "FOR_TASK", {"generate": [fakes["CHECKPOINT"], fakes["LIGHTNING"]],
                                             "upscale": [fakes["REALESRGAN"]], "cutout": [fakes["BIREFNET"]]})
    downloads = []

    def serve(request):
        downloads.append(str(request.url))
        data = blobs.get(str(request.url))
        return httpx.Response(200, content=data) if data else httpx.Response(404)

    comfy = FakeComfy()
    monkeypatch.setattr(img, "UPLOADS", str(uploads))
    monkeypatch.setattr(img, "MODELS_DIR", str(root))
    img.app.state.comfy_transport = comfy.transport
    img.app.state.download_transport = httpx.MockTransport(serve)
    img.app.state.comfy, img.app.state.downloads, img.app.state.blobs = comfy, downloads, blobs
    img.app.state.models_root = str(root)
    return img.app


# ------------------------------------------------------------------ handwriting and voice

@pytest.fixture
def reader_worker(tmp_path, monkeypatch):
    """The real reader worker with a fake vision model: a page with ink → its text, a blank page → [no text]."""
    import base64
    import io
    import json

    import httpx
    from PIL import Image, ImageStat
    from reader_worker import main as rd

    uploads = tmp_path / "uploads"
    uploads.mkdir(exist_ok=True)
    monkeypatch.setattr(rd, "UPLOADS", str(uploads))
    state = {"models": ["qwen3-vl:8b-instruct"], "pulled": [], "chats": 0}
    note = open(os.path.join(ROOT, "services/reader/reader_worker/selftest/note.txt")).read()

    def handle(request):
        path = request.url.path
        if path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": m} for m in state["models"]]})
        if path == "/api/pull":
            m = json.loads(request.content)["model"]
            state["pulled"].append(m)
            state["models"].append(m)
            lines = [{"status": "pulling", "digest": "sha256:a", "completed": 5, "total": 10},
                     {"status": "pulling", "digest": "sha256:a", "completed": 10, "total": 10}, {"status": "success"}]
            return httpx.Response(200, content="".join(json.dumps(x) + "\n" for x in lines))
        if path == "/api/chat":
            state["chats"] += 1
            img = Image.open(io.BytesIO(base64.b64decode(json.loads(request.content)["messages"][0]["images"][0])))
            blank = max(ImageStat.Stat(img.convert("L")).stddev) < 3
            reply = "[no text]" if blank else note
            lines = [{"message": {"content": reply}, "done": False},
                     {"message": {"content": ""}, "done": True, "done_reason": "stop"}]
            return httpx.Response(200, content="".join(json.dumps(x) + "\n" for x in lines))
        return httpx.Response(404)

    rd.app.state.transport = httpx.MockTransport(handle)
    rd.app.state.fake = state
    return rd.app


class FakeXTTS:
    sample_rate = 24000

    def __init__(self):
        self.loaded = False
        self.spoken = []

    def load(self):
        self.loaded = True

    def voices(self):
        return sorted(["Claribel Dervla", "Damien Black", "Ana Florence"] + [f"Voice {i:02d}" for i in range(55)])

    def speak(self, piece, language, voice):
        import numpy as np
        self.spoken.append(piece)
        n = int(0.06 * len(piece) * self.sample_rate)
        return (0.3 * np.sin(np.arange(n) * 2 * np.pi * 220 / self.sample_rate)).astype(np.float32)

    def unload(self):
        was, self.loaded = self.loaded, False
        return was


@pytest.fixture
def voice_worker(tmp_path, monkeypatch):
    """The real voice worker with a fake XTTS and a fake transcriber. Like Whisper, the fake writes numbers
    as digits ("six thirty" → "6.30", "eight" → "8"); it garbles a piece's first try when its text contains
    "glitch", as a real voice sometimes does."""
    import json

    import httpx
    from voice_worker import main as vw

    uploads = tmp_path / "uploads"
    uploads.mkdir(exist_ok=True)
    monkeypatch.setattr(vw, "UPLOADS", str(uploads))
    monkeypatch.setenv("XTTS_ACCEPT_CPML", "yes")
    tries = {}

    def listen(request):
        if request.url.path != "/transcribe":
            return httpx.Response(404)
        expect = json.loads(request.content)["expect"]
        tries[expect] = tries.get(expect, 0) + 1
        heard = expect.replace("six thirty", "6.30").replace("eight", "8")
        if "glitch" in expect and tries[expect] == 1:
            heard = "grumble mumble something else entirely"
        if "Tomorrow it will rain" in expect:  # the self-test's "different expected text": it heard the phrase
            heard = "Seven blue boxes arrived at the station on Tuesday morning"
        return httpx.Response(200, content=json.dumps({"type": "result", "text": heard}) + "\n")

    vw.app.state.engine = FakeXTTS()
    vw.app.state.transcriber_transport = httpx.MockTransport(listen)
    vw.app.state.tries = tries
    return vw.app
