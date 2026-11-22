"""Handwriting and voice: real workers with a fake vision model, a fake XTTS and a fake transcriber."""
import io
import json
import os
import shutil
import time
import wave

import httpx
import pytest
from fastapi.testclient import TestClient
from localai_gateway import main as gw
from PIL import Image
from voice_worker import text

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOTE = os.path.join(ROOT, "services/reader/reader_worker/selftest/note.jpg")


def make(fake, search_worker, tmp_path, workers, transports, **env):
    settings = gw.Settings({"OLLAMA_HOST": "http://fake-ollama", "DATA_DIR": str(tmp_path / "data"),
                            "UPLOADS_DIR": str(tmp_path / "uploads"), "LIBRARY_DIR": str(tmp_path / "library"),
                            "WORKERS": "search=http://search:8000 " + workers, **env})
    return gw.create_app(settings, ollama_transport=fake.transport, worker_transports={
        "search": httpx.ASGITransport(app=search_worker), **transports})


def ready(c):
    t0 = time.time()
    while c.get("/v1/status").json()["startup"]["phase"] != "ready":
        assert time.time() - t0 < 10
        time.sleep(0.05)
    return c


@pytest.fixture
def client(fake, search_worker, reader_worker, voice_worker, tmp_path):
    app = make(fake, search_worker, tmp_path, "reader=http://reader:8000 voice=http://voice:8000",
               {"reader": httpx.ASGITransport(app=reader_worker), "voice": httpx.ASGITransport(app=voice_worker)})
    with TestClient(app) as c:
        yield ready(c)


# ------------------------------------------------------------------ text for the voice

def test_numbers_in_english_and_italian():
    assert text.number_en(21) == "twenty-one" and text.number_en(105) == "one hundred and five"
    assert text.number_en(2500) == "two thousand five hundred"
    assert text.number_it(21) == "ventuno" and text.number_it(88) == "ottantotto"
    assert text.number_it(180) == "centottanta"
    assert text.number_it(1000) == "mille" and text.number_it(2026) == "duemilaventisei"


def test_spelling_times_years_decimals_codes():
    assert text.spell("at 10:30 in 2027", "en") == "at ten thirty in twenty twenty-seven"
    assert text.spell("at 9:05", "en") == "at nine oh five"
    assert text.spell("alle 9:30, 3,5 litri", "it") == "alle nove e trenta, tre virgola cinque litri"
    assert text.spell("code 0042", "en") == "code zero zero four two"
    assert text.spell("um 9:30", "de") == "um 9:30"  # other languages keep their digits


def test_pieces_drop_final_dots_and_quotes_and_pause_by_structure():
    p = text.pieces('He said "yes". Then he left.\n\nThe end.', "en")
    assert [x for x, _ in p] == ["He said yes", "Then he left", "The end"]
    assert [round(pause, 2) for _, pause in p] == [0.6, 1.1, 0.0]


def test_long_sentences_are_cut_at_commas_within_the_limit():
    long = ", ".join(f"item number {i} on the list" for i in range(30)) + "."
    p = text.pieces(long, "en", max_chars=120)
    assert len(p) > 3 and all(len(x) <= 120 for x, _ in p)
    assert not any(x.endswith(",") for x, _ in p)


def test_text_without_words_gives_no_pieces():
    assert text.pieces("… «» !", "en") == []


# ------------------------------------------------------------------ voice

def test_speak_returns_audio_listened_to(client, voice_worker):
    r = client.post("/v1/speak?wait=true", json={"text": "Welcome home. The heating is on.", "voice": "Damien Black"})
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    assert res["pieces"] == 2 and res["verify"]["checked"] and res["verify"]["min_score"] >= 0.85
    assert "non-commercial" in res["license"]
    f = client.get(res["file"])
    assert f.headers["content-type"] == "audio/wav"
    with wave.open(io.BytesIO(f.content)) as w:
        assert w.getframerate() == 24000 and abs(w.getnframes() / 24000 - res["duration"]) < 0.01


def test_a_garbled_piece_is_redone(client, voice_worker):
    res = client.post("/v1/speak?wait=true", json={"text": "One fine line. A glitch line here."}).json()["result"]
    assert res["verify"]["retried"] == 1 and res["verify"]["below"] == []
    assert voice_worker.state.engine.spoken.count("A glitch line here") == 2


def test_without_the_transcriber_the_audio_is_made_but_said_unchecked(client, voice_worker):
    def down(request):
        raise httpx.ConnectError("no transcriber")
    voice_worker.state.transcriber_transport = httpx.MockTransport(down)
    res = client.post("/v1/speak?wait=true", json={"text": "Hello there."}).json()["result"]
    assert res["verify"]["checked"] is False and "not running" in res["verify"]["note"]


def test_introduced_error_license_not_accepted(client, monkeypatch):
    monkeypatch.setenv("XTTS_ACCEPT_CPML", "")
    r = client.post("/v1/speak?wait=true", json={"text": "Hello."})
    assert r.status_code == 403 and "non-commercial" in r.json()["detail"]
    s = {x["name"]: x for x in client.get("/v1/services").json()["services"]}["voice"]
    assert s["state"] == "license_not_accepted" and "NON-COMMERCIAL" in s["license"]


def test_unknown_voice_and_no_words(client):
    r = client.post("/v1/speak?wait=true", json={"text": "Hello.", "voice": "Somebody Real"})
    assert r.status_code == 502 and "unknown voice" in r.json()["detail"]
    r = client.post("/v1/speak?wait=true", json={"text": "… «» !"})
    assert r.status_code == 502 and "no words" in r.json()["detail"]


def test_voices_list(client, voice_worker):
    v = client.get("/v1/voices").json()
    assert len(v["voices"]) == 58 and v["default"] == "Claribel Dervla"
    assert voice_worker.state.engine.loaded is False  # listing the voices does not put XTTS on the GPU


def test_first_voices_list_loads_the_model_inside_the_gpu_queue(fake, search_worker, voice_worker, tmp_path):
    # a fresh install: the names come only with the model, and loading it is GPU work
    class Spy(httpx.AsyncBaseTransport):
        def __init__(self, inner):
            self.inner, self.held, self.app = inner, [], None

        async def handle_async_request(self, request):
            self.held.append((request.url.query.decode(), self.app.state.jobs.gpu.locked()))
            return await self.inner.handle_async_request(request)

    voice_worker.state.engine.saved = False
    spy = Spy(httpx.ASGITransport(app=voice_worker))
    app = make(fake, search_worker, tmp_path, "voice=http://voice:8000", {"voice": spy})
    spy.app = app
    with TestClient(app) as c:
        ready(c)
        spy.held.clear()
        assert len(c.get("/v1/voices").json()["voices"]) == 58
    assert spy.held == [("", False), ("load=true", True)]


def test_real_engine_lists_voices_without_loading_unless_asked(monkeypatch):
    from voice_worker import main as vw
    monkeypatch.setenv("XTTS_ACCEPT_CPML", "yes")
    eng = vw.XTTS()

    def no_gpu():
        raise AssertionError("the model was loaded")
    monkeypatch.setattr(eng, "load", no_gpu)
    monkeypatch.setattr(vw, "saved_voices", lambda: ["Ana Florence", "Damien Black"])
    assert eng.voices(load=False) == eng.voices() == ["Ana Florence", "Damien Black"]
    monkeypatch.setattr(vw, "saved_voices", lambda: None)  # not downloaded yet
    assert eng.voices(load=False) is None


def test_saved_voices_reads_names_without_the_model(tmp_path):
    from voice_worker import main as vw
    assert vw.saved_voices(str(tmp_path / "missing.pth")) is None
    bad = tmp_path / "speakers_xtts.pth"
    bad.write_bytes(b"not a torch file")
    assert vw.saved_voices(str(bad)) is None  # unreadable: ask the model instead


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg encodes mp3 and ogg")
def test_ogg_and_mp3(client):
    for fmt, ctype in (("ogg", "audio/ogg"), ("mp3", "audio/mpeg")):
        res = client.post("/v1/speak?wait=true", json={"text": "Hello.", "format": fmt, "verify": False}).json()
        assert client.get(res["result"]["file"]).headers["content-type"] == ctype


def test_verifying_keeps_the_transcriber_loaded(fake, search_worker, voice_worker, tmp_path, monkeypatch):
    from test_images import FakeGpuWorker, full_gpu
    tr, other = FakeGpuWorker("transcriber"), FakeGpuWorker("image")
    tr_t = tr.transport

    def tr_info(request):  # it must be known as the transcribe service
        if request.url.path == "/info":
            return httpx.Response(200, json={"worker": "transcriber", "profile": "transcribe",
                                             "services": [{"name": "transcribe", "ollama": False}]})
        return tr_t.handle_request(request)
    app = make(fake, search_worker, tmp_path, "voice=http://voice:8000 transcriber=http://t:8000 image=http://i:8000",
               {"voice": httpx.ASGITransport(app=voice_worker), "transcriber": httpx.MockTransport(tr_info),
                "image": other.transport})
    full_gpu(monkeypatch)
    with TestClient(app) as c:
        ready(c).get("/v1/services")
        c.post("/v1/speak?wait=true", json={"text": "Hello."})
    assert other.unloads == 1 and tr.unloads == 0


# ------------------------------------------------------------------ handwriting

def test_handwriting_reads_a_note(client):
    with open(NOTE, "rb") as f:
        expected = open(NOTE.replace(".jpg", ".txt")).read()
        r = client.post("/v1/handwriting?wait=true", files={"file": ("note.jpg", f, "image/jpeg")},
                        data={"expect": expected})
    res = r.json()["result"]
    assert "plumber" in res["text"] and res["matches"] and not res["empty"]


def test_introduced_error_a_blank_page_is_not_read(client):
    buf = io.BytesIO()
    Image.new("RGB", (800, 600), (250, 248, 240)).save(buf, "PNG")
    res = client.post("/v1/handwriting?wait=true", files={"file": ("blank.png", buf.getvalue(), "image/png")}).json()
    assert res["result"]["empty"] and res["result"]["text"] == ""


def test_handwriting_downloads_its_model_once(client, reader_worker):
    reader_worker.state.fake["models"] = []
    with open(NOTE, "rb") as f:
        r = client.post("/v1/handwriting", files={"file": ("note.jpg", f.read(), "image/jpeg")})
    with client.stream("GET", f"/v1/jobs/{r.json()['id']}/events") as s:
        events = "".join(s.iter_text())
    assert "downloading qwen3-vl:8b-instruct" in events
    assert reader_worker.state.fake["pulled"] == ["qwen3-vl:8b-instruct"]


def test_handwriting_refuses_what_is_not_an_image(client):
    r = client.post("/v1/handwriting?wait=true", files={"file": ("note.png", b"not an image", "image/png")})
    assert r.status_code == 502 and "not an image" in r.json()["detail"]


def test_reader_selftest_with_fakes(reader_worker):
    lines = [json.loads(x) for x in TestClient(reader_worker).post("/selftest").text.splitlines()]
    checks = {x["name"]: x["ok"] for x in lines if x["type"] == "check"}
    assert all(checks.values()) and len(checks) == 3, checks


def test_voice_selftest_with_fakes(voice_worker):
    lines = [json.loads(x) for x in TestClient(voice_worker).post("/selftest").text.splitlines()]
    checks = {x["name"]: x for x in lines if x["type"] == "check"}
    assert all(c["ok"] for c in checks.values()), checks
    assert "listened to again by the transcriber" in checks and len(checks) == 7
    assert "the license notice travels inside the file" in checks


def test_numbers_whisper_writes_as_digits_still_match(client, voice_worker):
    said = "The heating has been on since 6:30, and dinner is at 8."
    res = client.post("/v1/speak?wait=true", json={"text": said}).json()
    v = res["result"]["verify"]
    assert v["checked"] and v["below"] == [] and v["retried"] == 0 and v["min_score"] >= 0.95


def test_heard_as_said():
    from voice_worker.main import similarity
    assert similarity("since 6.30 and dinner is at 8.", "since six thirty and dinner is at eight", "en") == 1.0
    assert similarity("alle 9.30", "alle nove e trenta", "it") == 1.0


def test_the_license_notice_travels_inside_the_wav(client, voice_worker, tmp_path):
    from voice_worker.main import LICENSE_URL, notice_of
    res = client.post("/v1/speak?wait=true", json={"text": "Hello there.", "verify": False}).json()["result"]
    path = tmp_path / "out.wav"
    path.write_bytes(client.get(res["file"]).content)
    n = notice_of(str(path))
    assert n and "non-commercial" in n and LICENSE_URL in n
    with wave.open(str(path)) as w:  # still a valid WAV for any player
        assert w.getframerate() == 24000 and w.getnframes() > 0


@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe reads the tags")
def test_the_license_notice_travels_inside_mp3_and_ogg(client, tmp_path):
    from voice_worker.main import LICENSE_URL, notice_of
    for fmt in ("mp3", "ogg"):
        res = client.post("/v1/speak?wait=true", json={"text": "Hello.", "format": fmt, "verify": False}).json()
        path = tmp_path / f"out.{fmt}"
        path.write_bytes(client.get(res["result"]["file"]).content)
        assert LICENSE_URL in (notice_of(str(path)) or ""), fmt


def test_handwriting_is_ready_before_its_model_is_downloaded(client, fake):
    fake.models = [m for m in fake.models if "qwen3-vl" not in m]  # a fresh install: not pulled yet
    s = {x["name"]: x for x in client.get("/v1/services").json()["services"]}["handwriting"]
    assert s["state"] == "ready" and "first use" in s["note"]
