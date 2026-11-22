"""local-ai-core gateway: the one public face of the stack.

Serves the /v1 API, its OpenAPI description (/openapi.json, /docs) and the web interface. Long work
runs as jobs (202 + job id; follow it with GET /v1/jobs/{id} or its event stream), and GPU jobs wait in
a single queue. The workers and Ollama sit behind it on the compose network.
"""
import asyncio
import contextlib
import hmac
import json
import logging
import os
import time
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

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
        self.static_dir = env.get("STATIC_DIR", os.path.join(os.path.dirname(HERE), "static"))
        self.workers = {}
        for item in env.get("WORKERS", "search=http://search:8000").split():
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
    app.state.jobs = JobStore()
    app.state.ollama = OllamaClient(settings.ollama_host, transport=ollama_transport)
    app.state.workers = {n: Worker(n, u, transport=worker_transports.get(n)) for n, u in settings.workers.items()}
    app.state.boot = {"phase": "starting", "detail": "", "pull": None, "error": None}
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
                if service in w.services or (not w.services and w.name == service):
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
                key = model_key(s["model"]) if s.get("model") else None
                if present is None:
                    state = "ollama_unreachable"
                elif key and key not in present:
                    state = "model_missing"
                else:
                    state = "ready"
                out.append({**s, "worker": w.name, "profile": info.get("profile"), "state": state,
                            "loaded": key in loaded, "vram_in_use_gb": loaded.get(key, 0.0),
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

def submit_index(app, w, rebuild=False):

    async def run(job):
        result = None
        async for ev in w.stream("/index", {"rebuild": rebuild}):
            if ev["type"] == "progress":
                await job.set_progress(ev["done"], ev["total"], "embedding chunks")
            elif ev["type"] == "result":
                result = {k: v for k, v in ev.items() if k != "type"}
        if result is None:
            raise JobFailed("the worker ended without a result")
        return result

    return app.state.jobs.submit("index", {"rebuild": rebuild}, run)


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
