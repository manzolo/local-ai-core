"""Talking to Ollama and to the workers, plus the GPU readout."""
import asyncio
import json
import shutil

import httpx

from .jobs import JobFailed


def model_key(name):
    """Ollama lists `bge-m3` as `bge-m3:latest`: compare names in that form."""
    return name if ":" in name.split("/")[-1] else name + ":latest"


class OllamaClient:
    def __init__(self, base_url, transport=None):
        self.base_url = base_url.rstrip("/")
        self.transport = transport

    def _client(self, timeout=5):
        return httpx.AsyncClient(base_url=self.base_url, transport=self.transport, timeout=timeout)

    async def get(self, path):
        async with self._client() as c:
            r = await c.get(path)
            r.raise_for_status()
            return r.json()

    async def version(self):
        return (await self.get("/api/version")).get("version")

    async def tags(self):
        return [m["name"] for m in (await self.get("/api/tags")).get("models", [])]

    async def loaded(self):
        out = []
        for m in (await self.get("/api/ps")).get("models", []):
            out.append({"name": m["name"], "vram_gb": round(m.get("size_vram", 0) / 2**30, 1),
                        "expires_at": m.get("expires_at")})
        return out

    async def unload(self, model):
        """Asks Ollama to free a model's VRAM now (keep_alive 0)."""
        async with self._client(30) as c:
            r = await c.post("/api/generate", json={"model": model, "keep_alive": 0})
            r.raise_for_status()

    async def pull(self, model, on_progress):
        """Downloads a model. Ollama reports progress per layer: on_progress(status, completed, total) gets
        the sum over the layers seen so far, so it describes the whole model."""
        layers = {}
        async with self._client(httpx.Timeout(None, connect=10)) as c:
            async with c.stream("POST", "/api/pull", json={"model": model, "stream": True}) as r:
                if r.status_code != 200:
                    raise JobFailed(f"pull of {model} failed: HTTP {r.status_code}")
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    d = json.loads(line)
                    if "error" in d:
                        raise JobFailed(f"pull of {model} failed: {d['error']}")
                    if d.get("digest") and d.get("total"):
                        layers[d["digest"]] = (d.get("completed") or 0, d["total"])
                    status = "downloading" if d.get("digest") else d.get("status", "")
                    done = sum(c for c, _ in layers.values())
                    total = sum(t for _, t in layers.values())
                    await on_progress(status, done or None, total or None)


class Worker:
    """A worker container on the compose network. It is never exposed outside it."""

    def __init__(self, name, url, transport=None):
        self.name = name
        self.url = url.rstrip("/")
        self.transport = transport
        self.services = []  # filled from /info

    def _client(self, timeout=10):
        return httpx.AsyncClient(base_url=self.url, transport=self.transport, timeout=timeout)

    async def info(self):
        async with self._client(3) as c:
            r = await c.get("/info")
            r.raise_for_status()
            return r.json()

    async def post(self, path, body, timeout=120):
        async with self._client(httpx.Timeout(timeout, connect=5)) as c:
            try:
                r = await c.post(path, json=body)
            except httpx.HTTPError as e:
                raise JobFailed(f"worker {self.name} not reachable: {e}") from None
        if r.status_code >= 400:
            raise WorkerHTTPError(r.status_code, _detail(r))
        return r.json()

    async def stream(self, path, body):
        """POST and yield the NDJSON events; an `error` event becomes a JobFailed."""
        try:
            async with self._client(httpx.Timeout(600, connect=5)) as c:
                async with c.stream("POST", path, json=body) as r:
                    if r.status_code >= 400:
                        await r.aread()
                        raise JobFailed(_detail(r))
                    async for line in r.aiter_lines():
                        if not line.strip():
                            continue
                        ev = json.loads(line)
                        if ev.get("type") == "error":
                            raise JobFailed(ev.get("message", "worker error"))
                        yield ev
        except httpx.HTTPError as e:
            raise JobFailed(f"worker {self.name} not reachable: {e}") from None


class WorkerHTTPError(RuntimeError):
    def __init__(self, status, detail):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _detail(r):
    try:
        return r.json().get("detail") or r.text
    except ValueError:
        return r.text[:300]


async def gpu_status():
    """Name, VRAM and load of the first NVIDIA GPU, or None when nvidia-smi is not available."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            exe, "--query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu",
            "--format=csv,noheader,nounits",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(), 3)
    except (TimeoutError, OSError):
        return None
    line = out.decode().strip().splitlines()[:1]
    if not line:
        return None
    try:
        name, used, total, util, temp = [x.strip() for x in line[0].split(",")]
        return {"name": name, "vram_used_gb": round(int(used) / 1024, 1),
                "vram_total_gb": round(int(total) / 1024, 1), "utilization": int(util),
                "temperature_c": int(temp)}
    except ValueError:
        return None
