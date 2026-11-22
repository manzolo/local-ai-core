"""Search worker: known notes → expected chunks, hits and answers, plus the errors it must catch."""
import json
import os

from fastapi.testclient import TestClient
from search_worker import ask, chunks
from search_worker import index as idx


def ndjson(r):
    return [json.loads(x) for x in r.text.splitlines() if x.strip()]


def test_chunks_carry_title_and_headings():
    text = "---\nstatus: open\n---\n# Heating\n\n## Pellet stove\n\nE5 means blocked exhaust.\n"
    cs = chunks.chunks_of(text, "Heating")
    assert cs[0]["text"] == "status: open"
    stove = [c for c in cs if "E5" in c["text"]][0]
    assert stove["heads"] == ["Heating", "Pellet stove"]
    assert chunks.embed_text(stove).startswith("Heating › Heating › Pellet stove")


def test_long_paragraph_is_split():
    text = "# T\n\n" + "\n".join(f"line {i} " + "x" * 100 for i in range(60))
    cs = chunks.chunks_of(text, "T")
    assert len(cs) > 1 and all(len(c["text"]) <= 2 * chunks.CHUNK_CHARS for c in cs)


def test_index_then_search_finds_the_right_note(search_worker):
    c = TestClient(search_worker)
    events = ndjson(c.post("/index", json={}))
    assert events[-1]["type"] == "result" and events[-1]["files"] == 6
    r = c.post("/search", json={"q": "bathroom tap plumber invoice", "n": 3})
    assert r.status_code == 200
    assert r.json()["results"][0]["file"] == "house/repairs.md"


def test_incremental_update_reads_only_changed_files(search_worker, notes):
    c = TestClient(search_worker)
    ndjson(c.post("/index", json={}))
    with open(notes / "kitchen" / "bread.md", "a") as f:
        f.write("\nAdd seeds on top.\n")
    res = ndjson(c.post("/index", json={}))[-1]
    assert (res["changed"], res["kept"], res["removed"]) == (1, 5, 0)


def test_introduced_error_truncated_index_is_refused(search_worker):
    c = TestClient(search_worker)
    ndjson(c.post("/index", json={}))
    base = search_worker.state.settings.index_base
    os.truncate(base + ".f32", os.path.getsize(base + ".f32") - 64)
    r = c.post("/search", json={"q": "stove", "update": False})
    assert r.status_code == 409 and "inconsistent" in r.json()["detail"]
    # the update path must refuse it too, not silently rebuild over it
    r = c.post("/search", json={"q": "stove"})
    assert r.status_code == 409


def test_ask_grounded_answer(search_worker, fake):
    fake.reply = "The plumber's invoice was 180 euros [1]."
    events = ndjson(TestClient(search_worker).post("/ask", json={"question": "bathroom tap invoice plumber"}))
    kinds = [e["type"] for e in events]
    assert kinds[0] == "sources" and "token" in kinds and kinds[-1] == "result"
    res = events[-1]
    assert res["grounded"] and res["cited"][0]["file"] == "house/repairs.md" and res["invalid"] == []


def test_introduced_error_invented_citation_is_flagged(search_worker, fake):
    fake.reply = "The invoice was 180 euros [1], paid by the neighbour [9]."
    res = ndjson(TestClient(search_worker).post("/ask", json={"question": "bathroom invoice", "n": 3}))[-1]
    assert res["invalid"] == [9] and not res["grounded"]


def test_no_excerpts_no_model_call(search_worker, fake):
    res = ndjson(TestClient(search_worker).post("/ask", json={"question": "zzz qqq", "min": 0.99}))[-1]
    assert res["answer"] == ask.NOT_FOUND and fake.chat_calls == 0


def test_ollama_down_is_an_error_event(search_worker):
    import httpx

    def down(request):
        raise httpx.ConnectError("refused")
    search_worker.state.transport = httpx.MockTransport(down)
    events = ndjson(TestClient(search_worker).post("/ask", json={"question": "stove"}))
    assert events[-1]["type"] == "error" and "not reachable" in events[-1]["message"]


def test_citations_parser():
    assert ask.citations("a [1] b [2, 3] c [1][7]", 3) == ([1, 2, 3], [7])


def test_info_declares_models_and_licenses(search_worker):
    info = TestClient(search_worker).get("/info").json()
    names = {s["name"]: s for s in info["services"]}
    assert set(names) == {"search", "ask"}
    assert all(s["license"] and s["vram_gb"] for s in names.values())


def test_index_load_rejects_wrong_version(tmp_path):
    import array
    base = str(tmp_path / "i")
    idx.save(base, {"version": 99, "dim": 2, "chunks": [{}], "files": {}}, array.array("f", [0, 0]))
    try:
        idx.load(base)
        raise AssertionError("a wrong-version index was accepted")
    except idx.IndexError_:
        pass
