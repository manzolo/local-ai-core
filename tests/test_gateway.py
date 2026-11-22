"""Gateway: the public API over a fake Ollama and the real search worker (in memory, no network)."""
import asyncio
import json
import re
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from localai_gateway import main as gw
from localai_gateway.jobs import JobStore


def make(fake, search_worker, tmp_path, **env):
    base = {"OLLAMA_HOST": "http://fake-ollama", "WORKERS": "search=http://search:8000",
            "DATA_DIR": str(tmp_path / "data")}
    base.update(env)
    return gw.create_app(gw.Settings(base), ollama_transport=fake.transport,
                         worker_transports={"search": httpx.ASGITransport(app=search_worker)})


def wait_ready(c, timeout=10):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = c.get("/v1/status").json()
        if st["startup"]["phase"] == "ready":
            return st
        if st["startup"]["phase"] == "error":
            raise AssertionError(st["startup"])
        time.sleep(0.05)
    raise AssertionError(f"not ready: {st['startup']}")


@pytest.fixture
def client(fake, search_worker, tmp_path):
    with TestClient(make(fake, search_worker, tmp_path)) as c:
        wait_ready(c)
        yield c


def test_startup_pulls_missing_models_and_indexes(fake, search_worker, tmp_path):
    fake.models = []
    with TestClient(make(fake, search_worker, tmp_path)) as c:
        st = wait_ready(c)
        assert fake.pulled == ["bge-m3", "qwen3.5:9b"]
        # progress covers the whole model, not the layer being downloaded
        assert c.app.state.pull_log[-1] == ("success", 400, 400)
        assert ("downloading", 350, 400) in c.app.state.pull_log
        assert st["ollama"]["reachable"] and st["ollama"]["loaded"][0]["name"] == "qwen3.5:9b"
        jobs = c.get("/v1/jobs").json()["jobs"]
        assert jobs[0]["kind"] == "index" and jobs[0]["status"] == "done"


def test_ask_wait_returns_grounded_answer_with_excerpts(client, fake):
    fake.reply = "It cost 180 euros [1]."
    r = client.post("/v1/ask?wait=true", json={"question": "bathroom tap plumber invoice"})
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    assert res["grounded"] and "180" in res["answer"]
    assert res["excerpts"][0]["file"] == "house/repairs.md"


def test_ask_job_and_event_stream(client):
    r = client.post("/v1/ask", json={"question": "bathroom invoice"})
    assert r.status_code == 202 and r.headers["location"].startswith("/v1/jobs/")
    job_id = r.json()["id"]
    with client.stream("GET", f"/v1/jobs/{job_id}/events") as s:
        text = "".join(s.iter_text())
    kinds = re.findall(r"^event: (\w+)$", text, re.M)
    assert kinds[0] == "status" and "sources" in kinds and "token" in kinds and "result" in kinds
    assert kinds[-1] == "status"
    assert client.get(f"/v1/jobs/{job_id}").json()["status"] == "done"
    # a client that reconnects gets only what it has not seen
    with client.stream("GET", f"/v1/jobs/{job_id}/events", headers={"Last-Event-ID": "2"}) as s:
        ids = [int(x) for x in re.findall(r"^id: (\d+)$", "".join(s.iter_text()), re.M)]
    assert ids[0] == 3


def test_search_endpoint(client):
    r = client.get("/v1/search", params={"q": "sourdough bread flour", "n": 2})
    assert r.status_code == 200 and r.json()["results"][0]["file"] == "kitchen/bread.md"


def test_services_catalogue(client):
    services = {s["name"]: s for s in client.get("/v1/services").json()["services"]}
    assert services["search"]["state"] == "ready" and services["ask"]["state"] == "ready"
    assert services["ask"]["loaded"] and services["ask"]["vram_in_use_gb"] == 7.0
    assert services["search"]["license"] == "MIT"


def test_services_report_missing_model_and_dead_worker(fake, search_worker, tmp_path):
    app = make(fake, search_worker, tmp_path, AUTO_PULL_MODELS="false", AUTO_INDEX="false",
               WORKERS="search=http://search:8000 voice=http://voice:8000")

    def dead(request):
        raise httpx.ConnectError("no such host")
    app.state.workers["voice"].transport = httpx.MockTransport(dead)
    fake.models = ["bge-m3:latest"]
    with TestClient(app) as c:
        services = {s["name"]: s for s in c.get("/v1/services").json()["services"]}
    assert services["ask"]["state"] == "model_missing"
    assert services["voice"]["state"] == "off"


def test_selftest_records_last_test(client):
    r = client.post("/v1/services/search/test?wait=true")
    assert r.status_code == 200
    checks = {c["name"]: c for c in r.json()["result"]["checks"]}
    # the fake embeddings know no synonyms, but the introduced errors must be caught regardless
    assert checks["introduced error: truncated index"]["ok"]
    assert checks["introduced error: invented citation"]["ok"]
    assert checks["incremental update"]["ok"]
    last = {s["name"]: s for s in client.get("/v1/services").json()["services"]}["search"]["last_test"]
    assert last and "ok" in last


def test_introduced_error_wrong_api_key_is_refused(fake, search_worker, tmp_path):
    with TestClient(make(fake, search_worker, tmp_path, LOCAL_AI_API_KEY="s3cret")) as c:
        assert c.get("/v1/status").status_code == 401
        assert c.get("/v1/status", headers={"X-API-Key": "wrong"}).status_code == 401
        assert c.get("/v1/status", headers={"X-API-Key": "s3cret"}).status_code == 200
        assert c.get("/v1/status", headers={"Authorization": "Bearer s3cret"}).status_code == 200
        assert c.get("/").status_code == 200 and c.get("/openapi.json").status_code == 200


def test_introduced_error_oversized_body_is_refused(fake, search_worker, tmp_path):
    with TestClient(make(fake, search_worker, tmp_path, MAX_UPLOAD_MB="1")) as c:
        big = json.dumps({"question": "x" * (2 * 2**20)})
        r = c.post("/v1/ask", content=big, headers={"content-type": "application/json"})
        assert r.status_code == 413

        def chunks():  # no Content-Length: the limit must hold for chunked bodies too
            for _ in range(3):
                yield b"x" * 2**20
        r = c.post("/v1/ask", content=chunks(), headers={"content-type": "application/json"})
        assert r.status_code == 413


def test_openapi_and_pages(client):
    spec = client.get("/openapi.json").json()
    for p in ("/v1/status", "/v1/services", "/v1/search", "/v1/search/index", "/v1/ask", "/v1/jobs/{job_id}",
              "/v1/jobs/{job_id}/events", "/v1/services/{name}/test"):
        assert p in spec["paths"], p
    assert spec["info"]["title"] == "Local AI Core"
    docs = client.get("/docs").text
    assert "/static/vendor/swagger/swagger-ui-bundle.js" in docs and "cdn" not in docs.lower()


def test_gpu_queue_runs_one_heavy_job_at_a_time():
    async def scenario():
        store, running, peak = JobStore(), 0, 0

        async def work(job):
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.05)
            running -= 1
            return "ok"
        jobs = [store.submit("x", {}, work) for _ in range(3)]
        await asyncio.sleep(0.01)
        assert store.queue()["waiting"] == 2
        for j in jobs:
            await j.wait()
        assert peak == 1 and all(j.status == "done" for j in jobs)
        assert store.queue() == {"running": None, "waiting": 0}
    asyncio.run(scenario())


def test_failing_job_reports_error_without_crashing():
    async def scenario():
        store = JobStore()

        async def boom(job):
            raise ValueError("bad input")
        j = store.submit("x", {}, boom)
        await j.wait()
        assert j.status == "error" and "bad input" in j.error
    asyncio.run(scenario())


def test_web_ui_is_self_contained_and_uses_only_the_public_api(client):
    import os
    static = os.path.join(os.path.dirname(gw.__file__), "..", "static")
    page = client.get("/").text
    assert "/static/app.js" in page and "/static/style.css" in page
    spec_paths = client.get("/openapi.json").json()["paths"]
    filled = [re.sub(r"\{[^}]+\}", "x", p) for p in spec_paths]  # /v1/jobs/{job_id} → /v1/jobs/x
    for name in ("index.html", "app.js", "style.css"):
        text = open(os.path.join(static, name), encoding="utf-8").read()
        # no CDN, no fonts from elsewhere: everything comes from the gateway itself
        assert not re.search(r"""(src|href)\s*=\s*["']https?://|@import|url\(\s*["']?https?://""", text), name
        for used in re.findall(r"""["'`](/v1/[a-z/]+)""", text):
            # "/v1/jobs/" is followed by an id in the code: it must start a real path
            probe = used + "x" if used.endswith("/") else used
            assert any(f == probe or f.startswith(probe + "/") for f in filled), used
