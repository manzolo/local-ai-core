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
    s.min_score = 0.05  # bag-of-words vectors score lower than real embeddings
    worker.app.state.settings = s
    worker.app.state.transport = fake.transport
    return worker.app
