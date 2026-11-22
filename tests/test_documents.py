"""Documents: the worker with a fake Docling, and the gateway chain upload → Markdown → library → ask."""
import os
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from localai_gateway import main as gw

INVOICE = (b"%PDF-1.4\nTEXT:# Electricity bill\nTEXT:| Item | Amount |\nTEXT:| Energy | 47.60 |\n"
           b"TEXT:| Total | 71.25 |\nTEXT:Paid by direct debit on 18 November.\n")


@pytest.fixture
def gateway(fake, search_worker, documents_worker, tmp_path):
    from documents_worker import main as docs
    settings = gw.Settings({"OLLAMA_HOST": "http://fake-ollama", "DATA_DIR": str(tmp_path / "data"),
                            "UPLOADS_DIR": docs.UPLOADS, "LIBRARY_DIR": search_worker.state.settings.library_dir,
                            "WORKERS": "search=http://search:8000 documents=http://documents:8000"})
    app = gw.create_app(settings, ollama_transport=fake.transport, worker_transports={
        "search": httpx.ASGITransport(app=search_worker), "documents": httpx.ASGITransport(app=documents_worker)})
    with TestClient(app) as c:
        t0 = time.time()
        while c.get("/v1/status").json()["startup"]["phase"] != "ready":
            assert time.time() - t0 < 10
            time.sleep(0.05)
        yield c


def test_convert_returns_markdown_and_cleans_up(gateway, documents_worker):
    from documents_worker import main as docs
    r = gateway.post("/v1/documents/convert?wait=true", files={"file": ("bill.pdf", INVOICE, "application/pdf")})
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    assert "# Electricity bill" in res["markdown"] and res["tables"] == 3 and res["library"] is None
    assert os.listdir(docs.UPLOADS) == []  # the upload is gone once the job ends


def test_convert_into_library_then_ask(gateway, fake):
    r = gateway.post("/v1/documents/convert?wait=true", files={"file": ("bill.pdf", INVOICE, "application/pdf")},
                     data={"add_to_library": "true"})
    res = r.json()["result"]
    assert res["library"] == "library/bill.md" and res["index"]["changed"] == 1
    assert [d["name"] for d in gateway.get("/v1/library").json()["documents"]] == ["bill.md"]
    fake.reply = "The total is 71.25 [1]."
    ans = gateway.post("/v1/ask?wait=true", json={"question": "electricity bill total energy"}).json()["result"]
    assert ans["grounded"] and ans["cited"][0]["file"] == "library/bill.md"
    # removing it takes it out of the index too
    assert gateway.delete("/v1/library/bill.md?wait=true").json()["result"]["removed"] == 1
    hits = gateway.get("/v1/search", params={"q": "electricity bill energy"}).json()["results"]
    assert all(h["file"] != "library/bill.md" for h in hits)


def test_introduced_error_scan_without_ocr_is_an_error(gateway):
    r = gateway.post("/v1/documents/convert?wait=true", files={"file": ("scan.pdf", b"%PDF SCAN", "application/pdf")},
                     data={"ocr": "off"})
    assert r.status_code == 502 and "no text extracted" in r.json()["detail"]
    r = gateway.post("/v1/documents/convert?wait=true", files={"file": ("scan.pdf", b"%PDF SCAN", "application/pdf")})
    assert r.status_code == 200 and "47.60" in r.json()["result"]["markdown"]


def test_introduced_error_broken_pdf_is_an_error(gateway):
    r = gateway.post("/v1/documents/convert?wait=true", files={"file": ("x.pdf", b"%PDF-1.4 nope", "application/pdf")})
    assert r.status_code == 502 and "Failed to load" in r.json()["detail"]


def test_unsupported_type_and_hostile_names(gateway):
    r = gateway.post("/v1/documents/convert", files={"file": ("run.sh", b"echo hi", "text/plain")})
    assert r.status_code == 415
    r = gateway.post("/v1/documents/convert?wait=true",
                     files={"file": ("../../etc/passwd.pdf", INVOICE, "application/pdf")},
                     data={"add_to_library": "true"})
    assert r.json()["result"]["library"] == "library/passwd.md"
    assert gateway.delete("/v1/library/..%2Fdata.md").status_code in (400, 404)
    assert gateway.delete("/v1/library/nothing.md").status_code == 404


def test_worker_refuses_paths_outside_uploads(documents_worker):
    c = TestClient(documents_worker)
    r = c.post("/convert", json={"path": "/etc/hostname", "out": "/tmp/x.md"})
    assert r.status_code == 400


def test_documents_in_catalogue_without_ollama_model(gateway):
    s = {x["name"]: x for x in gateway.get("/v1/services").json()["services"]}
    assert s["documents"]["state"] == "ready" and s["documents"]["profile"] == "docs"
    assert s["documents"]["vram_in_use_gb"] is None


def test_vram_guard_unloads_ollama_when_gpu_is_full(gateway, fake, monkeypatch):
    async def full_gpu():
        return {"name": "Fake GPU", "vram_used_gb": 11.0, "vram_total_gb": 12.0, "utilization": 0, "temperature_c": 40}
    monkeypatch.setattr(gw, "gpu_status", full_gpu)
    unloaded = []
    real = fake.handle

    def spy(request):
        if request.url.path == "/api/generate":
            import json
            body = json.loads(request.content)
            unloaded.append((body["model"], body["keep_alive"]))
            return httpx.Response(200, json={"done": True})
        return real(request)
    fake.transport = httpx.MockTransport(spy)
    gateway.app.state.ollama.transport = fake.transport
    r = gateway.post("/v1/documents/convert?wait=true", files={"file": ("bill.pdf", INVOICE, "application/pdf")})
    assert r.status_code == 200
    assert unloaded == [("qwen3.5:9b", 0)]


def test_worker_selftest_with_fake_converter_reports_checks(documents_worker):
    import json
    lines = [json.loads(x) for x in TestClient(documents_worker).post("/selftest").text.splitlines()]
    names = {x["name"]: x["ok"] for x in lines if x["type"] == "check"}
    # the fake reads no real PDF, but the two introduced errors must still be caught
    assert names["introduced error: a broken PDF"] is True
    assert lines[-1]["type"] == "result"
