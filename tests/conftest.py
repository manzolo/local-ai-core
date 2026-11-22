import os
import shutil

import pytest

os.environ.setdefault("LOCAL_AI_NO_APP", "1")  # the gateway module builds no app at import time

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
