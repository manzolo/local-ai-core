"""Transcription and speakers: real workers with a fake Whisper and a fake ECAPA, through the gateway."""
import os
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from localai_gateway import main as gw
from localai_gateway import subtitles

DIALOGUE = ("0.0 2.5 A Good morning, I am calling about the window.\n"
            "3.2 5.9 B When did you notice the crack?\n"
            "6.6 9.0 A Last Sunday, after the storm.\n"
            "9.7 12.4 B I can send someone on Thursday.\n")
ONE_VOICE = "0.0 2.0 B The library opens at nine.\n2.7 5.0 B It closes at six.\n5.7 8.1 B Books renew online.\n"


def client_for(fake, search_worker, speech_workers, tmp_path, workers=None):
    tr, spk, uploads = speech_workers
    settings = gw.Settings({"OLLAMA_HOST": "http://fake-ollama", "DATA_DIR": str(tmp_path / "data"),
                            "UPLOADS_DIR": str(uploads), "LIBRARY_DIR": str(tmp_path / "library"),
                            "WORKERS": workers or "search=http://search:8000 transcriber=http://transcriber:8000 "
                                                  "speakers=http://speakers:8000"})
    transports = {"search": httpx.ASGITransport(app=search_worker), "transcriber": httpx.ASGITransport(app=tr),
                  "speakers": httpx.ASGITransport(app=spk)}
    return TestClient(gw.create_app(settings, ollama_transport=fake.transport, worker_transports=transports))


@pytest.fixture
def client(fake, search_worker, speech_workers, tmp_path):
    with client_for(fake, search_worker, speech_workers, tmp_path) as c:
        t0 = time.time()
        while c.get("/v1/status").json()["startup"]["phase"] != "ready":
            assert time.time() - t0 < 10
            time.sleep(0.05)
        yield c


def post(c, content, name="call.ogg", **data):
    return c.post("/v1/transcribe?wait=true", files={"file": (name, content.encode(), "audio/ogg")},
                  data={k: str(v).lower() if isinstance(v, bool) else str(v) for k, v in data.items()})


def test_transcript_text_and_subtitles(client, speech_workers):
    r = post(client, DIALOGUE)
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    assert res["language"] == "en" and res["duration"] == 12.4 and res["speakers"] is None
    assert res["text"].startswith("Good morning") and len(res["segments"]) == 4
    assert res["srt"].startswith("1\n00:00:00,000 --> 00:00:02,500\nGood morning")
    assert "4\n00:00:09,700 --> 00:00:12,400\nI can send someone" in res["srt"]
    assert res["vtt"].startswith("WEBVTT\n\n00:00:00.000 --> 00:00:02.500\n")
    assert os.listdir(speech_workers[2]) == []  # the upload is gone


def test_progress_events_follow_the_audio(client):
    r = client.post("/v1/transcribe", files={"file": ("call.ogg", DIALOGUE.encode(), "audio/ogg")})
    job = r.json()["id"]
    with client.stream("GET", f"/v1/jobs/{job}/events") as s:
        text = "".join(s.iter_text())
    assert '"message": "transcribing"' in text and '"done": 12.4, "total": 12.4' in text


def test_speakers_alternate(client):
    res = post(client, DIALOGUE, speakers=True).json()["result"]
    assert res["speakers"] == 2 and [s["speaker"] for s in res["segments"]] == [1, 2, 1, 2]
    assert "Speaker 2: When did you notice" in res["srt"]
    assert res["text"].count("Speaker ") == 4


def test_introduced_error_one_voice_stays_one_speaker(client):
    res = post(client, ONE_VOICE, speakers=True).json()["result"]
    assert res["speakers"] == 1 and {s["speaker"] for s in res["segments"]} == {1}


def test_exact_number_of_speakers(client):
    res = post(client, DIALOGUE, speakers=True, num_speakers=1).json()["result"]
    assert res["speakers"] == 1


def test_expected_text_matches_or_not(client):
    good = "Good morning, I am calling about the window. When did you notice the crack? Last Sunday, after " \
           "the storm. I can send someone on Thursday."
    res = post(client, DIALOGUE, expect=good).json()["result"]
    assert res["matches"] and res["score"] == 1.0
    res = post(client, DIALOGUE, expect="Tomorrow it will rain on the hills and nobody will leave.").json()["result"]
    assert not res["matches"] and res["score"] < 0.85


def test_introduced_error_not_audio(client):
    r = post(client, "NOTAUDIO")
    assert r.status_code == 502 and "cannot decode" in r.json()["detail"]


def test_unsupported_type(client):
    assert post(client, DIALOGUE, name="notes.txt").status_code == 415


def test_speakers_requested_but_profile_off(fake, search_worker, speech_workers, tmp_path):
    with client_for(fake, search_worker, speech_workers, tmp_path,
                    workers="search=http://search:8000 transcriber=http://transcriber:8000") as c:
        r = post(c, DIALOGUE, speakers=True)
    assert r.status_code == 503 and "speakers" in r.json()["detail"]


def test_catalogue_lists_speech_services(client):
    s = {x["name"]: x for x in client.get("/v1/services").json()["services"]}
    assert s["transcribe"]["profile"] == "transcribe" and s["speakers"]["profile"] == "speakers"
    assert s["transcribe"]["state"] == "ready" and s["speakers"]["license"] == "Apache-2.0"


def test_subtitle_stamps():
    assert subtitles.stamp(3723.4567, ",") == "01:02:03,457"
    assert subtitles.stamp(0.0016, ".") == "00:00:00.002"
    assert subtitles.stamp(-1, ",") == "00:00:00,000"


def test_speakers_numbered_in_order_of_first_speech():
    from speakers_worker.main import assign
    segs = [[0, 2, "a"], [2.5, 3, "short"], [3.5, 6, "b"], [6.5, 9, "c"]]
    # the clustering called the first voice "1" and the second "0": the output must still start at Speaker 1;
    # the short sentence (not measured) takes the speaker of the nearest measured one
    assert assign(segs, [0, 2, 3], [1, 0, 1]) == (2, [1, 1, 2, 1])


def test_selftest_by_worker_name_or_service_name(client):
    for name in ("transcriber", "transcribe"):
        r = client.post(f"/v1/services/{name}/test?wait=true")
        assert r.status_code == 200, (name, r.text)
        assert r.json()["params"]["worker"] == "transcriber"
