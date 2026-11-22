"""Images: the real image worker (fake ComfyUI, fake model server) through the gateway."""
import io
import json
import os
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from localai_gateway import jobs as jobs_mod
from localai_gateway import main as gw
from PIL import Image, ImageDraw


def photo_bytes():
    img = Image.new("RGB", (64, 64), (30, 58, 138))
    ImageDraw.Draw(img).ellipse((12, 12, 52, 52), fill=(220, 38, 38))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def make(fake, search_worker, image_worker, tmp_path, extra=None, **env):
    workers = "search=http://search:8000 image=http://image:8000" + (" " + " ".join(extra) if extra else "")
    settings = gw.Settings({"OLLAMA_HOST": "http://fake-ollama", "DATA_DIR": str(tmp_path / "data"),
                            "UPLOADS_DIR": str(tmp_path / "uploads"), "LIBRARY_DIR": str(tmp_path / "library"),
                            "WORKERS": workers, **env})
    return gw.create_app(settings, ollama_transport=fake.transport, worker_transports={
        "search": httpx.ASGITransport(app=search_worker), "image": httpx.ASGITransport(app=image_worker)})


@pytest.fixture
def client(fake, search_worker, image_worker, tmp_path):
    with TestClient(make(fake, search_worker, image_worker, tmp_path)) as c:
        t0 = time.time()
        while c.get("/v1/status").json()["startup"]["phase"] != "ready":
            assert time.time() - t0 < 10
            time.sleep(0.05)
        yield c


def png(c, res):
    r = c.get(res["file"])
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    return Image.open(io.BytesIO(r.content))


def test_generate_returns_a_png_of_the_asked_size(client):
    r = client.post("/v1/images/generate?wait=true", json={"prompt": "a lighthouse", "width": 768, "height": 512,
                                                            "seed": 7})
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    assert (res["width"], res["height"], res["seed"]) == (768, 512, 7)
    assert png(client, res).size == (768, 512)


def test_generate_picks_a_seed_when_none_is_given(client):
    a = client.post("/v1/images/generate?wait=true", json={"prompt": "x", "width": 512, "height": 512}).json()
    assert isinstance(a["result"]["seed"], int)


def test_generate_validates_its_parameters(client):
    for bad in ({"prompt": ""}, {"prompt": "x", "width": 513}, {"prompt": "x", "steps": 9},
                {"prompt": "x", "height": 4096}):
        assert client.post("/v1/images/generate", json=bad).status_code == 422, bad


def test_upscale_x4_and_x2(client):
    for scale, size in ((4, (256, 256)), (2, (128, 128))):
        r = client.post("/v1/images/upscale?wait=true", files={"file": ("p.png", photo_bytes(), "image/png")},
                        data={"scale": str(scale)})
        assert r.status_code == 200, r.text
        assert png(client, r.json()["result"]).size == size


def test_cutout_subject_opaque_background_transparent(client):
    r = client.post("/v1/images/cutout?wait=true", files={"file": ("p.png", photo_bytes(), "image/png")})
    img = png(client, r.json()["result"]).convert("RGBA")
    assert img.getpixel((32, 32))[3] >= 200 and img.getpixel((2, 2))[3] <= 50


def test_cutout_on_a_solid_background(client):
    r = client.post("/v1/images/cutout?wait=true", files={"file": ("p.png", photo_bytes(), "image/png")},
                    data={"background": "#00ff00"})
    img = png(client, r.json()["result"])
    assert img.mode == "RGB" and img.getpixel((2, 2)) == (0, 255, 0) and img.getpixel((32, 32))[0] > 150


def test_wrong_type_and_background(client):
    assert client.post("/v1/images/cutout", files={"file": ("x.pdf", b"%PDF", "application/pdf")}).status_code == 415
    assert client.post("/v1/images/cutout", files={"file": ("p.png", photo_bytes(), "image/png")},
                       data={"background": "red; rm -rf"}).status_code == 422


def test_models_download_once_and_progress_is_reported(client, image_worker):
    r = client.post("/v1/images/upscale", files={"file": ("p.png", photo_bytes(), "image/png")})
    job = r.json()["id"]
    with client.stream("GET", f"/v1/jobs/{job}/events") as s:
        text = "".join(s.iter_text())
    assert "downloading RealESRGAN_x4plus.pth" in text
    client.post("/v1/images/upscale?wait=true", files={"file": ("p.png", photo_bytes(), "image/png")})
    assert sum("RealESRGAN" in u for u in image_worker.state.downloads) == 1


def test_introduced_error_a_download_with_the_wrong_sha256_is_refused(client, image_worker):
    url = next(u for u in image_worker.state.blobs if "birefnet" in u)
    image_worker.state.blobs[url] = b"tampered" * 100
    r = client.post("/v1/images/cutout?wait=true", files={"file": ("p.png", photo_bytes(), "image/png")})
    assert r.status_code == 502 and "sha256" in r.json()["detail"]
    root = image_worker.state.models_root
    assert os.listdir(os.path.join(root, "background_removal")) == []  # neither the file nor a .part


def test_introduced_error_unknown_checkpoint(client, image_worker):
    image_worker.state.comfy.checkpoints = {"something-else.safetensors"}
    r = client.post("/v1/images/generate?wait=true", json={"prompt": "x", "width": 512, "height": 512})
    assert r.status_code == 502 and "not in list" in r.json()["detail"]


def test_uploads_are_cleaned_and_files_leave_with_their_job(client, tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_mod, "KEEP", 1)
    first = client.post("/v1/images/upscale?wait=true", files={"file": ("p.png", photo_bytes(), "image/png")}).json()
    path = os.path.join(tmp_path / "data" / "results", f"{first['id']}.png")
    assert os.path.isfile(path)
    assert os.listdir(tmp_path / "uploads") == []
    client.post("/v1/images/upscale?wait=true", files={"file": ("p.png", photo_bytes(), "image/png")})
    assert not os.path.exists(path)  # the first job left memory, its PNG with it
    assert client.get(f"/v1/jobs/{first['id']}/file").status_code == 404


def test_catalogue_states_licenses_and_restrictions(client):
    s = {x["name"]: x for x in client.get("/v1/services").json()["services"]}["images"]
    assert s["profile"] == "image" and "OpenRAIL" in s["license"] and "GPL" in s["license"]
    assert "harm" in s["use_restrictions"]


def test_worker_selftest_with_fakes_passes_including_introduced_errors(image_worker):
    lines = [json.loads(x) for x in TestClient(image_worker).post("/selftest").text.splitlines()]
    checks = {x["name"]: x for x in lines if x["type"] == "check"}
    assert all(c["ok"] for c in checks.values()), checks
    assert len(checks) == 5 and lines[-1] == {"type": "result", "ok": True}


# ------------------------------------------------------------------ the GPU is shared: VRAM is made, not hoped for

class FakeGpuWorker:
    def __init__(self, name):
        self.name, self.unloads = name, 0
        self.transport = httpx.MockTransport(self.handle)

    def handle(self, request):
        if request.url.path == "/unload":
            self.unloads += 1
            return httpx.Response(200, json={"unloaded": True})
        if request.url.path == "/info":
            return httpx.Response(200, json={"worker": self.name, "profile": "x", "services": []})
        return httpx.Response(404)


def full_gpu(monkeypatch, used=11.0):
    async def gpu():
        return {"name": "Fake GPU", "vram_used_gb": used, "vram_total_gb": 12.0, "utilization": 0,
                "temperature_c": 40}
    monkeypatch.setattr(gw, "gpu_status", gpu)


def test_a_full_gpu_frees_ollama_and_the_other_workers_before_an_image(fake, search_worker, image_worker,
                                                                       tmp_path, monkeypatch):
    other = FakeGpuWorker("transcriber")
    app = make(fake, search_worker, image_worker, tmp_path, extra=["transcriber=http://transcriber:8000"])
    app.state.workers["transcriber"].transport = other.transport
    full_gpu(monkeypatch)
    with TestClient(app) as c:
        r = c.post("/v1/images/generate", json={"prompt": "x", "width": 512, "height": 512})
        job = r.json()["id"]
        with c.stream("GET", f"/v1/jobs/{job}/events") as s:
            text = "".join(s.iter_text())
    assert other.unloads == 1 and '"worker": "transcriber"' in text and '"model": "qwen3.5:9b"' in text


def test_ask_frees_the_image_worker_but_keeps_its_own_model(fake, search_worker, image_worker, tmp_path,
                                                            monkeypatch):
    painter = FakeGpuWorker("image")
    app = make(fake, search_worker, image_worker, tmp_path)
    app.state.workers["image"].transport = painter.transport
    full_gpu(monkeypatch)
    fake_ps = fake.handle

    def nothing_loaded(request):  # the LLM is not in memory: room must be made for it
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": []})
        return fake_ps(request)
    fake.transport = httpx.MockTransport(nothing_loaded)
    app.state.ollama.transport = fake.transport
    with TestClient(app) as c:
        c.post("/v1/ask?wait=true", json={"question": "bathroom invoice"})
    assert painter.unloads == 1


def test_ask_does_not_unload_anything_when_its_model_is_already_loaded(fake, search_worker, image_worker,
                                                                       tmp_path, monkeypatch):
    painter = FakeGpuWorker("image")
    app = make(fake, search_worker, image_worker, tmp_path)
    app.state.workers["image"].transport = painter.transport
    full_gpu(monkeypatch)  # the fake Ollama reports qwen3.5:9b loaded
    with TestClient(app) as c:
        c.post("/v1/ask?wait=true", json={"question": "bathroom invoice"})
    assert painter.unloads == 0


def test_upscale_refuses_other_scales(client):
    for bad in ("3", "8", "1"):
        r = client.post("/v1/images/upscale", files={"file": ("p.png", photo_bytes(), "image/png")},
                        data={"scale": bad})
        assert r.status_code == 422, bad


def test_a_worker_restarting_to_free_vram_is_waited_for(fake, search_worker, image_worker, tmp_path, monkeypatch):
    from localai_gateway import clients
    monkeypatch.setattr(clients.asyncio, "sleep", _no_sleep)
    real = httpx.ASGITransport(app=image_worker)
    refused = {"n": 0}

    class Restarting(httpx.AsyncBaseTransport):  # the first connections are refused, as while restarting
        async def handle_async_request(self, request):
            if refused["n"] < 3:
                refused["n"] += 1
                raise httpx.ConnectError("connection refused")
            return await real.handle_async_request(request)

    app = make(fake, search_worker, image_worker, tmp_path)
    with TestClient(app) as c:
        app.state.workers["image"].transport = Restarting()
        r = c.post("/v1/images/upscale?wait=true", files={"file": ("p.png", photo_bytes(), "image/png")})
    assert r.status_code == 200, r.text
    assert refused["n"] == 3


async def _no_sleep(_):
    return None


def test_unload_restarts_the_worker_only_when_something_was_loaded(monkeypatch):
    from transcriber_worker import main as tr
    calls = []
    monkeypatch.setattr(tr, "restart_soon", lambda: calls.append(1))

    class Loaded:
        def unload(self):
            return True
    tr.app.state.engine = Loaded()
    assert TestClient(tr.app).post("/unload").json() == {"unloaded": True} and calls == [1]

    class Empty:
        def unload(self):
            return False
    tr.app.state.engine = Empty()
    assert TestClient(tr.app).post("/unload").json() == {"unloaded": False} and calls == [1]


def test_switching_task_frees_comfyui_models_first(client, image_worker):
    comfy = image_worker.state.comfy
    image_worker.state.last_task = None
    client.post("/v1/images/generate?wait=true", json={"prompt": "x", "width": 512, "height": 512})
    client.post("/v1/images/generate?wait=true", json={"prompt": "y", "width": 512, "height": 512})
    assert comfy.frees == 0  # same kind of job: SDXL stays, the second picture is fast
    client.post("/v1/images/cutout?wait=true", files={"file": ("p.png", photo_bytes(), "image/png")})
    assert comfy.frees == 1  # SDXL out before BiRefNet's pass


def test_a_service_test_makes_room_on_the_gpu_first(fake, search_worker, image_worker, tmp_path, monkeypatch):
    other = FakeGpuWorker("transcriber")
    app = make(fake, search_worker, image_worker, tmp_path, extra=["transcriber=http://transcriber:8000"])
    app.state.workers["transcriber"].transport = other.transport
    full_gpu(monkeypatch)
    with TestClient(app) as c:
        r = c.post("/v1/services/images/test?wait=true")
    assert r.status_code == 200 and other.unloads == 1


def test_status_shows_the_workers_holding_a_model(fake, search_worker, image_worker, tmp_path):
    class Holding(FakeGpuWorker):
        def handle(self, request):
            if request.url.path == "/info":
                return httpx.Response(200, json={"worker": self.name, "profile": "x", "services": [
                    {"name": "transcribe", "title": "Speech to text", "ollama": False, "loaded": True, "vram_gb": 5}]})
            return super().handle(request)

    def dead(request):
        raise httpx.ConnectError("off")
    app = make(fake, search_worker, image_worker, tmp_path,
               extra=["transcriber=http://t:8000", "voice=http://v:8000"])
    app.state.workers["transcriber"].transport = Holding("transcriber").transport
    app.state.workers["voice"].transport = httpx.MockTransport(dead)
    with TestClient(app) as c:
        st = c.get("/v1/status").json()
    held = {w["service"]: w for w in st["workers"]}
    assert held["transcribe"]["vram_gb_estimate"] == 5  # loaded: listed, with its estimate
    assert "images" not in held  # ComfyUI not running: holds nothing
    assert st["ollama"]["loaded"][0]["name"] == "qwen3.5:9b"  # Ollama's own models stay
