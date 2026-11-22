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
