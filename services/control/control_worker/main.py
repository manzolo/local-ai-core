"""Control worker: builds, starts and stops the optional services, for the buttons of the web page.

It is the only container that can talk to Docker, so it is kept small and closed (profile `control`, off
by default):
  - it knows six services by name and three actions; nothing else is accepted, there is no free argument
    and no shell: every command is a fixed list of words;
  - it never touches the gateway, Ollama, the search worker or itself (`--no-deps`);
  - the project folder is mounted read-only: what gets built is what is in the repository;
  - one action at a time; every action is logged;
  - it has no published port: only the gateway reaches it, on the compose network, and the gateway allows
    these actions only on 127.0.0.1 or with an API key.

Internal API:
  GET  /health                     liveness
  GET  /services                   for each optional service: profile, image built or not, running or not
  POST /services/{name}/{action}   action = build | start | stop → NDJSON: log lines, then the result
"""
import asyncio
import json
import os
import time
from enum import StrEnum

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

VERSION = "0.1.0"
PROJECT_DIR = os.environ.get("PROJECT_DIR", "")
IMAGE_TAG = "0.1.0"


class Service(StrEnum):
    documents = "documents"
    transcribe = "transcribe"
    speakers = "speakers"
    images = "images"
    handwriting = "handwriting"
    voice = "voice"


class Action(StrEnum):
    build = "build"
    start = "start"
    stop = "stop"


# catalogue name → (compose service, compose profile)
SERVICES = {
    Service.documents: ("documents", "docs"),
    Service.transcribe: ("transcriber", "transcribe"),
    Service.speakers: ("speakers", "speakers"),
    Service.images: ("image", "image"),
    Service.handwriting: ("reader", "handwriting"),
    Service.voice: ("voice", "voice"),
}


def compose(profile, *words):
    """The only shape of command this worker runs: docker compose, this project, one profile."""
    return ["docker", "compose", "--project-directory", PROJECT_DIR, "--profile", profile, *words]


def command(name: Service, action: Action):
    svc, profile = SERVICES[name]
    if action is Action.build:
        return compose(profile, "build", svc)
    if action is Action.start:
        # --no-deps: never recreate the gateway or anything else; --no-build: start what was built
        return compose(profile, "up", "-d", "--no-deps", "--no-build", svc)
    return compose(profile, "stop", svc)


async def run(cmd, timeout=None):
    """→ (exit code, output lines). Used for the quick state queries."""
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.STDOUT)
    out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    return proc.returncode, out.decode(errors="replace").splitlines()


async def stream(cmd):
    """Runs cmd in the project folder and yields its output line by line, then its exit code. The folder
    matters: compose resolves the relative paths of COMPOSE_FILE (family mode) against it."""
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.STDOUT, cwd=PROJECT_DIR or None)
    async for raw in proc.stdout:
        line = raw.decode(errors="replace").rstrip()
        if line:
            yield {"type": "log", "line": line[:500]}
    yield {"type": "exit", "code": await proc.wait()}


app = FastAPI(title="local-ai-core control worker", version=VERSION)
app.state.lock = asyncio.Lock()
app.state.runner = stream  # tests replace it
app.state.query = run


@app.get("/health")
async def health():
    return {"ok": bool(PROJECT_DIR)}


@app.get("/services")
async def services():
    q = app.state.query
    code, lines = await q(["docker", "ps", "--format", "{{.Label \"com.docker.compose.service\"}}",
                           "--filter", "label=com.docker.compose.project=local-ai-core"], 20)
    running = set(lines) if code == 0 else set()
    out = []
    for name, (svc, profile) in SERVICES.items():
        code, _ = await q(["docker", "image", "inspect", "--format", "{{.Id}}",
                           f"local-ai-core/{svc}:{IMAGE_TAG}"], 20)
        out.append({"name": name.value, "compose_service": svc, "profile": profile, "built": code == 0,
                    "running": svc in running})
    return {"services": out, "busy": app.state.lock.locked()}


@app.post("/services/{name}/{action}")
async def act(name: Service, action: Action):
    if not PROJECT_DIR:
        raise HTTPException(503, "PROJECT_DIR is not set: the project folder is not mounted")
    if app.state.lock.locked():
        raise HTTPException(409, "another action is running: one at a time")
    await app.state.lock.acquire()
    cmd = command(name, action)
    print(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "action": action.value,
                      "service": name.value}), flush=True)  # the audit log: docker compose logs control

    async def body():
        try:
            t0, code = time.time(), None
            async for ev in app.state.runner(cmd):
                if ev["type"] == "exit":
                    code = ev["code"]
                else:
                    yield json.dumps(ev) + "\n"
            if code == 0:
                yield json.dumps({"type": "result", "service": name.value, "action": action.value,
                                  "secs": round(time.time() - t0, 1)}) + "\n"
            else:
                hint = " (build it first)" if action is Action.start else ""
                yield json.dumps({"type": "error", "message": f"{action.value} of {name.value} failed "
                                                             f"(exit {code}){hint}"}) + "\n"
        finally:
            app.state.lock.release()
    return StreamingResponse(body(), media_type="application/x-ndjson")
