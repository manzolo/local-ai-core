"""local-ai-core gateway: the one public face of the stack.

Serves the /v1 API, its OpenAPI description (/openapi.json, /docs) and the web interface. Long work
runs as jobs (202 + job id; follow it with GET /v1/jobs/{id} or its event stream), and GPU jobs wait in
a single queue. The workers and Ollama sit behind it on the compose network.
"""
import asyncio
import collections
import contextlib
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import time
import uuid
from typing import Any, Literal

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import subtitles
from .clients import OllamaClient, Worker, WorkerHTTPError, gpu_status, model_key
from .jobs import JobFailed, JobStore

VERSION = "0.1.0"
HERE = os.path.dirname(os.path.abspath(__file__))
log = logging.getLogger("gateway")


class Settings:
    def __init__(self, env=os.environ):
        self.ollama_host = env.get("OLLAMA_HOST", "http://ollama:11434")
        self.llm_model = env.get("OLLAMA_MODEL", "qwen3.5:9b")
        self.embed_model = env.get("EMBED_MODEL", "bge-m3")
        self.api_key = env.get("LOCAL_AI_API_KEY", "")
        self.max_upload_mb = int(env.get("MAX_UPLOAD_MB", "50"))
        self.auto_pull = env.get("AUTO_PULL_MODELS", "true").lower() in ("1", "true", "yes")
        self.auto_index = env.get("AUTO_INDEX", "true").lower() in ("1", "true", "yes")
        self.data_dir = env.get("DATA_DIR", "/data")
        self.uploads_dir = env.get("UPLOADS_DIR", "/data/uploads")
        self.library_dir = env.get("LIBRARY_DIR", "/library")
        self.static_dir = env.get("STATIC_DIR", os.path.join(os.path.dirname(HERE), "static"))
        self.workers = {}
        default_workers = ("search=http://search:8000 documents=http://documents:8000 "
                           "transcriber=http://transcriber:8000 speakers=http://speakers:8000 image=http://image:8000")
        for item in env.get("WORKERS", default_workers).split():
            name, _, url = item.partition("=")
            if name and url:
                self.workers[name] = url


# ------------------------------------------------------------------------------------- body limit

class BodyTooLarge(Exception):
    pass


class BodyLimit:
    """Refuses request bodies over the limit with 413, declared or not (chunked uploads count too)."""

    def __init__(self, app, limit_bytes):
        self.app = app
        self.limit = limit_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared and declared.isdigit() and int(declared) > self.limit:
            return await self._refuse(send)
        state = {"received": 0, "over": False, "started": False, "sent": False}

        async def counting_receive():
            msg = await receive()
            if msg["type"] == "http.request":
                state["received"] += len(msg.get("body", b""))
                if state["received"] > self.limit:
                    state["over"] = True
                    raise BodyTooLarge
            return msg

        async def guarded_send(msg):
            # The framework may turn the exception into its own 400: once over the limit, the answer is 413.
            if state["over"]:
                if not state["sent"]:
                    state["sent"] = True
                    await self._refuse(send)
                return
            if msg["type"] == "http.response.start":
                state["started"] = True
            await send(msg)

        try:
            await self.app(scope, counting_receive, guarded_send)
        except BodyTooLarge:
            if not state["started"] and not state["sent"]:
                state["sent"] = True
                await self._refuse(send)

    async def _refuse(self, send):
        body = json.dumps({"detail": f"request body over the limit of {self.limit // 2**20} MB "
                                     "(MAX_UPLOAD_MB)"}).encode()
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


# ------------------------------------------------------------------------------------------ app

def create_app(settings=None, ollama_transport=None, worker_transports=None):
    settings = settings or Settings()
    worker_transports = worker_transports or {}

    @contextlib.asynccontextmanager
    async def lifespan(app):
        # result files belong to jobs, and jobs live in memory: a restart starts clean
        shutil.rmtree(results_dir(settings), ignore_errors=True)
        os.makedirs(results_dir(settings), exist_ok=True)
        app.state.boot_task = asyncio.create_task(bootstrap(app))
        yield
        app.state.boot_task.cancel()
        await app.state.jobs.close()

    app = FastAPI(
        title="Local AI Core", version=VERSION, lifespan=lifespan, docs_url=None, redoc_url=None,
        description="Local AI services on your own GPU behind one API: semantic search over your notes and "
                    "answers that cite them, more services as profiles. Long work runs as jobs.",
    )
    app.state.settings = settings
    app.state.jobs = JobStore(on_evict=drop_file)
    app.state.ollama = OllamaClient(settings.ollama_host, transport=ollama_transport)
    app.state.workers = {n: Worker(n, u, transport=worker_transports.get(n)) for n, u in settings.workers.items()}
    app.state.boot = {"phase": "starting", "detail": "", "pull": None, "error": None}
    app.state.pull_log = collections.deque(maxlen=50)  # last progress updates, for tests and debugging
    app.state.tests = load_tests(settings)
    app.add_middleware(BodyLimit, limit_bytes=settings.max_upload_mb * 2**20)

    def require_key(request: Request):
        key = settings.api_key
        if not key:
            return
        got = request.headers.get("x-api-key", "")
        auth = request.headers.get("authorization", "")
        if not got and auth.lower().startswith("bearer "):
            got = auth[7:]
        if not got or not hmac.compare_digest(got.encode(), key.encode()):
            raise HTTPException(401, "missing or wrong API key (X-API-Key header)",
                                headers={"WWW-Authenticate": "Bearer"})

    v1 = Depends(require_key)

    # ----------------------------------------------------------------------------- jobs plumbing

    def job_response(job, wait_result=None):
        if wait_result is not None:
            return wait_result
        return JSONResponse(job.public(), status_code=202, headers={"Location": f"/v1/jobs/{job.id}"})

    async def maybe_wait(job, wait):
        if not wait:
            return job_response(job)
        await job.wait()
        if job.status == "error":
            raise HTTPException(502, job.error)
        return JSONResponse(job.public())

    async def worker_for(service):
        """The worker that offers a service; asks the workers again when it is not known yet."""
        for attempt in range(2):
            for w in app.state.workers.values():
                if service in w.services or w.name == service:  # a service name, or the worker's own
                    return w
            if attempt == 0:
                for w in app.state.workers.values():
                    with contextlib.suppress(Exception):
                        w.services = [x["name"] for x in (await w.info())["services"]]
        raise HTTPException(503, f"no running worker offers {service!r} (is its compose profile enabled?)")

    # --------------------------------------------------------------------------------- endpoints

    @app.get("/healthz", include_in_schema=False)
    async def healthz():
        return {"ok": True}

    @app.get("/v1/status", dependencies=[v1], tags=["system"], summary="GPU, models in memory, queue, startup")
    async def status():
        ol = app.state.ollama
        ollama = {"url": settings.ollama_host, "reachable": False, "version": None, "loaded": []}
        try:
            ollama["version"] = await ol.version()
            ollama["loaded"] = await ol.loaded()
            ollama["reachable"] = True
        except Exception:  # noqa: BLE001 — status reports, it does not fail
            pass
        return {"version": VERSION, "startup": app.state.boot, "gpu": await gpu_status(), "ollama": ollama,
                "queue": app.state.jobs.queue(), "auth": bool(settings.api_key),
                "models": {"llm": settings.llm_model, "embeddings": settings.embed_model}}

    @app.get("/v1/services", dependencies=[v1], tags=["system"],
             summary="Catalogue: state, model, VRAM estimated and in use, last test, license")
    async def services():
        try:
            present = {model_key(m) for m in await app.state.ollama.tags()}
            loaded = {model_key(m["name"]): m["vram_gb"] for m in await app.state.ollama.loaded()}
        except Exception:  # noqa: BLE001
            present, loaded = None, {}
        out = []
        for w in app.state.workers.values():
            try:
                info = await w.info()
            except Exception:  # noqa: BLE001
                out.append({"name": w.name, "worker": w.name, "state": "off",
                            "detail": "worker not running (is its compose profile enabled?)"})
                continue
            w.services = [s["name"] for s in info.get("services", [])]
            for s in info.get("services", []):
                uses_ollama = s.get("ollama", True)
                key = model_key(s["model"]) if s.get("model") and uses_ollama else None
                if not uses_ollama:
                    state = "ready"
                elif present is None:
                    state = "ollama_unreachable"
                elif key and key not in present:
                    state = "model_missing"
                else:
                    state = "ready"
                is_loaded = key in loaded if uses_ollama else bool(s.get("loaded"))
                out.append({**s, "worker": w.name, "profile": info.get("profile"), "state": state,
                            "loaded": is_loaded, "vram_in_use_gb": loaded.get(key, 0.0) if uses_ollama else None,
                            "last_test": app.state.tests.get(w.name)})
        return {"services": out}

    @app.post("/v1/services/{name}/test", dependencies=[v1], tags=["system"], status_code=202,
              summary="Run the built-in test of a service: known data, expected output, introduced errors")
    async def test_service(name: str, wait: bool = False):
        w = await worker_for(name)

        async def run(job):
            checks, ok = [], False
            async for ev in w.stream("/selftest", {}):
                if ev["type"] == "check":
                    checks.append({k: ev[k] for k in ("name", "ok", "detail")})
                    await job.emit("check", checks[-1])
                    await job.set_progress(len(checks), None, ev["name"])
                elif ev["type"] == "progress":  # e.g. models downloading before the first check
                    await job.set_progress(ev.get("done"), ev.get("total"), ev.get("message"))
                elif ev["type"] == "result":
                    ok = ev["ok"]
            return {"ok": ok, "checks": checks}

        async def record(job):
            if job.status == "done":
                app.state.tests[w.name] = {"date": time.strftime("%Y-%m-%d %H:%M"), "ok": job.result["ok"]}
            else:
                app.state.tests[w.name] = {"date": time.strftime("%Y-%m-%d %H:%M"), "ok": False}
            save_tests(settings, app.state.tests)

        job = app.state.jobs.submit("test", {"service": name, "worker": w.name}, run, on_done=record)
        return await maybe_wait(job, wait)

    @app.get("/v1/search", dependencies=[v1], tags=["search"], response_model=SearchOut,
             summary="Search the notes by meaning")
    async def search(q: str = Query(min_length=1, max_length=2000), n: int = Query(8, ge=1, le=50),
                     min: float | None = Query(None, ge=-1, le=1, description="minimum score (default 0.35)")):
        try:
            return await (await worker_for("search")).post("/search", {"q": q, "n": n, "min": min})
        except WorkerHTTPError as e:
            raise HTTPException(e.status, e.detail) from None
        except JobFailed as e:
            raise HTTPException(503, str(e)) from None

    @app.post("/v1/search/index", dependencies=[v1], tags=["search"], status_code=202,
              summary="Bring the index of the notes folder up to date (job)")
    async def search_index(req: IndexIn | None = None, wait: bool = False):
        return await maybe_wait(submit_index(app, await worker_for("search"), (req or IndexIn()).rebuild), wait)

    @app.post("/v1/ask", dependencies=[v1], tags=["search"], status_code=202,
              summary="Question → answer built only from the notes, with checked citations (job)")
    async def ask(req: AskIn, wait: bool = False):
        w = await worker_for("ask")
        body = req.model_dump()

        async def run(job):
            sources, result = [], None
            await ensure_vram(app, job, 7, keep_model=req.model or settings.llm_model)
            async for ev in w.stream("/ask", body):
                if ev["type"] == "sources":
                    sources = ev["sources"]
                    await job.emit("sources", sources)
                elif ev["type"] == "token":
                    await job.emit("token", ev["text"])
                elif ev["type"] == "result":
                    result = {k: v for k, v in ev.items() if k != "type"}
            if result is None:
                raise JobFailed("the worker ended without an answer")
            result["excerpts"] = sources
            return result

        job = app.state.jobs.submit("ask", {"question": req.question}, run)
        return await maybe_wait(job, wait)

    @app.post("/v1/documents/convert", dependencies=[v1], tags=["documents"], status_code=202,
              summary="PDF (scanned too), image, DOCX, PPTX, XLSX, HTML → Markdown; optionally into the library (job)")
    async def documents_convert(
            file: UploadFile = File(description="the document"),
            ocr: Literal["auto", "force", "off"] = Form("auto", description="auto: OCR only where a page has "
                                                         "no text; force: OCR every page; off: never"),
            lang: str = Form("en", pattern=r"^[a-z]{2,8}$", description="OCR language (one)"),
            tables: bool = Form(True, description="recognise table structure"),
            add_to_library: bool = Form(False, description="save the Markdown in the library and index it, "
                                                           "so /v1/ask can answer from it"),
            wait: bool = False):
        name = safe_name(file.filename or "document")
        ext = os.path.splitext(name)[1].lower()
        if ext not in DOC_TYPES:
            raise HTTPException(415, f"unsupported file type {ext or '(none)'}: one of {', '.join(sorted(DOC_TYPES))}")
        w = await worker_for("documents")
        job_dir, src = await save_upload(settings, file, name)

        async def run(job):
            try:
                await job.set_progress(0, None, "converting")
                await ensure_vram(app, job, 3, exclude=w.name)
                out = os.path.join(job_dir, "out.md")
                try:
                    stats = await w.post("/convert", {"path": src, "out": out, "ocr": ocr, "lang": lang,
                                                      "tables": tables}, timeout=1800)
                except WorkerHTTPError as e:
                    raise JobFailed(e.detail) from None
                with open(out, encoding="utf-8") as f:
                    md = f.read()
                result = {"filename": name, "markdown": md, **stats, "library": None, "index": None}
                if add_to_library:
                    lib_name = os.path.splitext(name)[0] + ".md"
                    os.makedirs(settings.library_dir, exist_ok=True)
                    with open(os.path.join(settings.library_dir, lib_name), "w", encoding="utf-8") as f:
                        f.write(md)
                    result["library"] = f"library/{lib_name}"
                    await job.set_progress(0, None, "indexing")
                    result["index"] = await run_index(await worker_for("search"), job)
                return result
            finally:
                shutil.rmtree(job_dir, ignore_errors=True)  # uploads never outlive their job

        job = app.state.jobs.submit("documents", {"filename": name, "ocr": ocr, "add_to_library": add_to_library}, run)
        return await maybe_wait(job, wait)

    @app.post("/v1/transcribe", dependencies=[v1], tags=["speech"], status_code=202,
              summary="Audio or video → text, SRT and WebVTT; optionally who said what, and a check against "
                      "the text the speech should say (job)")
    async def transcribe(
            file: UploadFile = File(description="audio or video"),
            language: str = Form("auto", pattern=r"^(auto|[a-z]{2,3})$", description="spoken language, or auto"),
            speakers: bool = Form(False, description="split the transcript by speaker (profile `speakers`)"),
            num_speakers: int = Form(0, ge=0, le=20, description="exact number of speakers; 0 = find out"),
            expect: str | None = Form(None, max_length=20000,
                                      description="the text the speech should say: the result scores the match"),
            wait: bool = False):
        name = safe_name(file.filename or "audio")
        ext = os.path.splitext(name)[1].lower()
        if ext not in AUDIO_TYPES:
            raise HTTPException(415, f"unsupported file type {ext or '(none)'}: "
                                     f"one of {', '.join(sorted(AUDIO_TYPES))}")
        w = await worker_for("transcribe")
        sw = await worker_for("speakers") if speakers else None
        job_dir, src = await save_upload(settings, file, name)

        async def run(job):
            try:
                await ensure_vram(app, job, 5, exclude=w.name)
                res = None
                body = {"path": src, "language": language, "expect": expect}
                async for ev in w.stream("/transcribe", body):
                    if ev["type"] == "progress":
                        await job.set_progress(ev["done"], ev["total"], "transcribing")
                    elif ev["type"] == "result":
                        res = ev
                if res is None:
                    raise JobFailed("the transcriber ended without a result")
                segs = [{"start": a, "end": b, "text": t} for a, b, t in res["segments"]]
                n_speakers = None
                if sw and segs:
                    await job.set_progress(res["duration"], res["duration"], "speakers")
                    await ensure_vram(app, job, 1, exclude=sw.name)
                    try:
                        att = await sw.post("/attribute", {"path": src, "segments": res["segments"],
                                                           "n": num_speakers}, timeout=1800)
                    except WorkerHTTPError as e:
                        raise JobFailed(e.detail) from None
                    n_speakers = att["speakers"]
                    for seg, label in zip(segs, att["labels"], strict=True):
                        seg["speaker"] = label
                out = {"filename": name, "language": res["language"], "duration": res["duration"],
                       "speakers": n_speakers, "segments": segs, "text": subtitles.text(segs),
                       "srt": subtitles.srt(segs), "vtt": subtitles.vtt(segs)}
                if expect is not None:
                    out.update(expected=expect, score=res["score"], matches=res["matches"])
                return out
            finally:
                shutil.rmtree(job_dir, ignore_errors=True)

        job = app.state.jobs.submit("transcribe", {"filename": name, "speakers": speakers}, run)
        return await maybe_wait(job, wait)

    async def image_job(kind, path, body, params, src_dir=None):
        w = await worker_for("images")
        job_dir = src_dir or os.path.join(settings.uploads_dir, uuid.uuid4().hex[:12])
        os.makedirs(job_dir, exist_ok=True)

        async def run(job):
            try:
                await ensure_vram(app, job, 8, exclude=w.name)
                out, res = os.path.join(job_dir, "out.png"), None
                async for ev in w.stream(path, {**body, "out": out}):
                    if ev["type"] == "progress":
                        await job.set_progress(ev.get("done"), ev.get("total"), ev.get("message"))
                    elif ev["type"] == "result":
                        res = {k: v for k, v in ev.items() if k != "type"}
                if res is None:
                    raise JobFailed("the image worker ended without a result")
                dest = os.path.join(results_dir(settings), f"{job.id}.png")
                shutil.move(out, dest)
                job.file = dest
                return {**res, "file": f"/v1/jobs/{job.id}/file"}
            finally:
                shutil.rmtree(job_dir, ignore_errors=True)

        return app.state.jobs.submit(kind, params, run)

    @app.post("/v1/images/generate", dependencies=[v1], tags=["images"], status_code=202,
              summary="Text → image with SDXL and the Lightning LoRA, 4 steps (job; the PNG at /v1/jobs/{id}/file)")
    async def images_generate(req: GenerateIn, wait: bool = False):
        seed = req.seed if req.seed is not None else secrets.randbelow(2**32)
        body = {**req.model_dump(exclude={"seed"}), "seed": seed}
        job = await image_job("image", "/generate", body, {"prompt": req.prompt, "seed": seed})
        return await maybe_wait(job, wait)

    @app.post("/v1/images/upscale", dependencies=[v1], tags=["images"], status_code=202,
              summary="Photo → ×2 or ×4 with RealESRGAN (job; the PNG at /v1/jobs/{id}/file)")
    async def images_upscale(file: UploadFile = File(description="the photo"),
                             scale: int = Form(4, ge=2, le=4, multiple_of=2, description="2 or 4"),
                             wait: bool = False):
        job_dir, src = await save_image_upload(settings, file)
        job = await image_job("upscale", "/upscale", {"path": src, "scale": scale},
                              {"filename": os.path.basename(src), "scale": scale}, job_dir)
        return await maybe_wait(job, wait)

    @app.post("/v1/images/cutout", dependencies=[v1], tags=["images"], status_code=202,
              summary="Photo → subject on a transparent (or solid) background with BiRefNet (job; PNG)")
    async def images_cutout(file: UploadFile = File(description="the photo"),
                            background: str = Form("", pattern=r"^(|white|black|#[0-9a-fA-F]{6})$",
                                                   description="empty = transparent; white, black or #rrggbb"),
                            wait: bool = False):
        job_dir, src = await save_image_upload(settings, file)
        job = await image_job("cutout", "/cutout", {"path": src, "background": background},
                              {"filename": os.path.basename(src), "background": background}, job_dir)
        return await maybe_wait(job, wait)

    @app.get("/v1/jobs/{job_id}/file", dependencies=[v1], tags=["jobs"], summary="The file a job produced (PNG)",
             response_class=FileResponse)
    async def job_file(job_id: str):
        job = app.state.jobs.get(job_id)
        if not job or not job.file or not os.path.isfile(job.file):
            raise HTTPException(404, "this job has no file (or it is gone: files live as long as their job)")
        return FileResponse(job.file, media_type="image/png", filename=f"{job.kind}-{job.id}.png")

    @app.get("/v1/library", dependencies=[v1], tags=["documents"], summary="Documents added to the library")
    async def library_list():
        d, out = settings.library_dir, []
        if os.path.isdir(d):
            for n in sorted(os.listdir(d)):
                p = os.path.join(d, n)
                if n.endswith(".md") and os.path.isfile(p):
                    st = os.stat(p)
                    out.append({"name": n, "path": f"library/{n}", "bytes": st.st_size, "modified": st.st_mtime})
        return {"documents": out}

    @app.delete("/v1/library/{name}", dependencies=[v1], tags=["documents"], status_code=202,
                summary="Remove a document from the library and from the index (job)")
    async def library_delete(name: str, wait: bool = False):
        if not LIB_NAME.match(name):
            raise HTTPException(400, "not a library document name")
        p = os.path.join(settings.library_dir, name)
        if not os.path.isfile(p):
            raise HTTPException(404, "no such document in the library")
        os.remove(p)
        return await maybe_wait(submit_index(app, await worker_for("search")), wait)

    @app.get("/v1/jobs", dependencies=[v1], tags=["jobs"], summary="Recent jobs, newest first")
    async def jobs_list(limit: int = Query(20, ge=1, le=200)):
        jobs = list(app.state.jobs.jobs.values())[::-1][:limit]
        return {"jobs": [j.public() for j in jobs], "queue": app.state.jobs.queue()}

    @app.get("/v1/jobs/{job_id}", dependencies=[v1], tags=["jobs"], response_model=JobOut,
             summary="State and result of a job")
    async def job_get(job_id: str):
        job = app.state.jobs.get(job_id)
        if not job:
            raise HTTPException(404, "no such job (jobs are kept in memory: a restart forgets them)")
        return job.public()

    @app.get("/v1/jobs/{job_id}/events", dependencies=[v1], tags=["jobs"],
             summary="Follow a job as Server-Sent Events (status, progress, sources, token, check, result, error)")
    async def job_events(job_id: str, request: Request, after: int = Query(0, ge=0)):
        job = app.state.jobs.get(job_id)
        if not job:
            raise HTTPException(404, "no such job")
        last = request.headers.get("last-event-id")
        start = int(last) if last and last.isdigit() else after

        async def gen():
            async for ev in job.follow(start):
                yield f"id: {ev['seq']}\nevent: {ev['type']}\ndata: {json.dumps(ev['data'], ensure_ascii=False)}\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ------------------------------------------------------------------------------ web pages

    static = settings.static_dir
    if os.path.isdir(static):
        app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(os.path.join(static, "index.html"), headers={"Cache-Control": "no-cache"})

    @app.get("/docs", include_in_schema=False)
    async def docs():
        # Swagger UI from files baked into the image: no CDN, works offline.
        return get_swagger_ui_html(openapi_url="/openapi.json", title="Local AI Core — API",
                                   swagger_js_url="/static/vendor/swagger/swagger-ui-bundle.js",
                                   swagger_css_url="/static/vendor/swagger/swagger-ui.css",
                                   swagger_favicon_url="/static/favicon.svg")

    return app


# ----------------------------------------------------------------------------------- models

class IndexIn(BaseModel):
    rebuild: bool = Field(False, description="throw the index away and read every file again")


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000, examples=["How much did the bathroom repair cost?"])
    n: int = Field(6, ge=1, le=20, description="how many excerpts the model reads")
    min: float | None = Field(None, ge=-1, le=1, description="minimum search score (default 0.35)")
    model: str | None = Field(None, description="Ollama model (default OLLAMA_MODEL)")


class GenerateIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000, examples=["a lighthouse on a cliff at sunset, oil painting"])
    negative: str = Field("blurry, deformed, low quality, text, watermark", max_length=2000,
                          description="what to avoid")
    width: int = Field(1024, ge=512, le=1536, multiple_of=8)
    height: int = Field(1024, ge=512, le=1536, multiple_of=8)
    steps: int = Field(4, ge=1, le=8, description="the Lightning LoRA is made for 4")
    seed: int | None = Field(None, ge=0, le=2**32 - 1, description="the same seed gives the same picture")


class Hit(BaseModel):
    score: float
    file: str
    line: int
    heads: list[str]
    title: str
    text: str


class SearchOut(BaseModel):
    results: list[Hit]


class JobOut(BaseModel):
    id: str
    kind: str
    status: Literal["queued", "running", "done", "error"]
    params: dict[str, Any]
    created: float
    started: float | None
    finished: float | None
    progress: dict[str, Any] | None
    result: Any
    error: str | None
    events: int = Field(description="events so far: follow them at /v1/jobs/{id}/events")


# ------------------------------------------------------------------------- startup and state

AUDIO_TYPES = {".wav", ".mp3", ".ogg", ".oga", ".opus", ".m4a", ".flac", ".aac", ".wma", ".webm", ".mp4",
               ".mkv", ".mov", ".avi"}
DOC_TYPES = {".pdf", ".docx", ".pptx", ".xlsx", ".html", ".htm", ".png", ".jpg", ".jpeg", ".tif", ".tiff",
             ".bmp", ".webp"}
LIB_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,150}\.md$")


IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


def results_dir(settings):
    return os.path.join(settings.data_dir, "results")


def drop_file(job):
    if job.file:
        with contextlib.suppress(OSError):
            os.remove(job.file)


async def save_image_upload(settings, file):
    name = safe_name(file.filename or "photo.png")
    ext = os.path.splitext(name)[1].lower()
    if ext not in IMAGE_TYPES:
        raise HTTPException(415, f"unsupported file type {ext or '(none)'}: one of {', '.join(sorted(IMAGE_TYPES))}")
    return await save_upload(settings, file, name)


async def save_upload(settings, file, name):
    """The upload into its own folder of the uploads volume: (folder, path). The job deletes the folder."""
    job_dir = os.path.join(settings.uploads_dir, uuid.uuid4().hex[:12])
    os.makedirs(job_dir, exist_ok=True)
    src = os.path.join(job_dir, name)
    with open(src, "wb") as f:
        while chunk := await file.read(2**20):
            f.write(chunk)
    await file.close()
    return job_dir, src


def safe_name(filename):
    """A file name that is safe on disk: no folders, only letters, digits, dot, dash, underscore."""
    base = os.path.basename(filename.replace("\\", "/")).strip() or "document"
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base).lstrip("._") or "document"
    return base[:150]


async def ensure_vram(app, job, needed_gb, exclude=None, keep_model=None):
    """Before GPU work: if the GPU has less free VRAM than the job needs, free what others hold now instead of
    waiting for their idle timeout: Ollama's models (except keep_model, the one the job is about to use),
    then the other GPU workers. Jobs run one at a time, so nothing is taken from a running job.
    Without nvidia-smi there is nothing to measure."""
    gpu = await gpu_status()
    if not gpu:
        return
    free = gpu["vram_total_gb"] - gpu["vram_used_gb"]
    try:
        loaded = await app.state.ollama.loaded()
    except Exception:  # noqa: BLE001
        loaded = []
    if keep_model and model_key(keep_model) in {model_key(m["name"]) for m in loaded}:
        return  # the model is already in memory: nothing to make room for
    if free >= needed_gb:
        return
    for m in loaded:
        if keep_model and model_key(m["name"]) == model_key(keep_model):
            continue
        await job.emit("unload", {"model": m["name"], "vram_gb": m["vram_gb"], "free_gb": round(free, 1),
                                  "needed_gb": needed_gb})
        with contextlib.suppress(Exception):
            await app.state.ollama.unload(m["name"])
    for w in app.state.workers.values():
        if w.name == exclude:
            continue
        with contextlib.suppress(Exception):
            if (await w.post("/unload", {}, timeout=60)).get("unloaded"):
                await job.emit("unload", {"worker": w.name, "free_gb": round(free, 1), "needed_gb": needed_gb})


async def run_index(w, job, rebuild=False):
    result = None
    async for ev in w.stream("/index", {"rebuild": rebuild}):
        if ev["type"] == "progress":
            await job.set_progress(ev["done"], ev["total"], "embedding chunks")
        elif ev["type"] == "result":
            result = {k: v for k, v in ev.items() if k != "type"}
    if result is None:
        raise JobFailed("the worker ended without a result")
    return result


def submit_index(app, w, rebuild=False):
    return app.state.jobs.submit("index", {"rebuild": rebuild}, lambda job: run_index(w, job, rebuild))


async def bootstrap(app):
    """Wait for Ollama, download the models that are missing, index the notes. Retries on failure."""
    s, boot, ol = app.state.settings, app.state.boot, app.state.ollama
    delay = 2
    while True:
        try:
            boot.update(phase="waiting_ollama", detail=s.ollama_host, error=None)
            while True:
                try:
                    await ol.version()
                    break
                except Exception:  # noqa: BLE001
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 15)
            if s.auto_pull:
                present = {model_key(m) for m in await ol.tags()}
                for m in dict.fromkeys([s.embed_model, s.llm_model]):
                    if model_key(m) in present:
                        continue
                    boot.update(phase="pulling", detail=m, pull={"model": m, "status": "starting"})

                    async def progress(status, completed, total, m=m):
                        boot["pull"] = {"model": m, "status": status, "completed": completed, "total": total}
                        app.state.pull_log.append((status, completed, total))
                    await ol.pull(m, progress)
                boot["pull"] = None
            if s.auto_index:
                boot.update(phase="indexing", detail="notes")
                w = None
                for _ in range(60):  # the worker may still be starting
                    w = next((x for x in app.state.workers.values() if "search" in x.services), None)
                    if w:
                        break
                    for x in app.state.workers.values():
                        with contextlib.suppress(Exception):
                            x.services = [i["name"] for i in (await x.info())["services"]]
                    if not any("search" in x.services for x in app.state.workers.values()):
                        await asyncio.sleep(1)
                if w is None:
                    raise RuntimeError("the search worker does not answer")
                job = submit_index(app, w)
                boot["job"] = job.id
                await job.wait()
                if job.status == "error":
                    raise RuntimeError(f"indexing failed: {job.error}")
            boot.update(phase="ready", detail="", error=None)
            return
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("startup: %s", e)
            boot.update(phase="error", error=str(e), detail="retrying in 30 s")
            await asyncio.sleep(30)


def tests_path(settings):
    return os.path.join(settings.data_dir, "tests.json")


def load_tests(settings):
    try:
        with open(tests_path(settings), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_tests(settings, tests):
    try:
        os.makedirs(settings.data_dir, exist_ok=True)
        tmp = tests_path(settings) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(tests, f)
        os.replace(tmp, tests_path(settings))
    except OSError as e:
        log.warning("cannot save test results: %s", e)


app = create_app() if os.environ.get("LOCAL_AI_NO_APP") != "1" else None
