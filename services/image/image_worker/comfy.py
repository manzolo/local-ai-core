"""ComfyUI as a separate program: started on demand on 127.0.0.1, spoken to over HTTP, stopped when idle.

ComfyUI is GPL-3.0 and runs unmodified in its own process; this module only starts it and uses its HTTP
API (/prompt, /history, /view, /upload/image).
"""
import asyncio
import os
import shutil
import signal
import subprocess

import httpx


class ComfyError(RuntimeError):
    pass


class ComfyProcess:
    """The ComfyUI process. Stopping it gives back all of its VRAM, CUDA context included."""

    def __init__(self, comfy_dir, data_dir, port=8188):
        self.comfy_dir = comfy_dir
        self.data_dir = data_dir
        self.port = port
        self.proc = None
        self.url = f"http://127.0.0.1:{port}"
        self.log_path = os.path.join(data_dir, "comfyui.log")

    @property
    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def _prepare(self, models_dir):
        # the model folders ComfyUI expects, inside the models volume
        dist = os.path.join(self.comfy_dir, "models.dist")
        for d in os.listdir(dist) if os.path.isdir(dist) else []:
            os.makedirs(os.path.join(models_dir, d), exist_ok=True)
        for d in ("input", "output", "temp", "user"):
            os.makedirs(os.path.join(self.data_dir, d), exist_ok=True)

    async def start(self, models_dir, timeout=180):
        if self.running:
            return
        self._prepare(models_dir)
        log = open(self.log_path, "ab")
        d = self.data_dir
        self.proc = subprocess.Popen(
            ["python", "main.py", "--listen", "127.0.0.1", "--port", str(self.port), "--disable-auto-launch",
             "--input-directory", f"{d}/input", "--output-directory", f"{d}/output",
             "--temp-directory", f"{d}/temp", "--user-directory", f"{d}/user"],
            cwd=self.comfy_dir, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        async with httpx.AsyncClient(timeout=3) as c:
            for _ in range(timeout * 2):
                if not self.running:
                    raise ComfyError(f"ComfyUI stopped while starting: {self.tail()}")
                try:
                    if (await c.get(f"{self.url}/system_stats")).status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.5)
        self.stop()
        raise ComfyError(f"ComfyUI did not start in {timeout} s: {self.tail()}")

    def stop(self):
        if not self.running:
            self.proc = None
            return False
        os.killpg(self.proc.pid, signal.SIGTERM)
        try:
            self.proc.wait(20)
        except subprocess.TimeoutExpired:
            os.killpg(self.proc.pid, signal.SIGKILL)
        self.proc = None
        shutil.rmtree(os.path.join(self.data_dir, "temp"), ignore_errors=True)
        return True

    def tail(self, n=3):
        try:
            with open(self.log_path, "rb") as f:
                lines = f.read()[-4000:].decode(errors="replace").strip().splitlines()
            return " | ".join(lines[-n:])
        except OSError:
            return ""


class ComfyClient:
    def __init__(self, url, transport=None):
        self.url = url.rstrip("/")
        self.transport = transport

    def _client(self, timeout=30):
        return httpx.AsyncClient(base_url=self.url, transport=self.transport, timeout=timeout)

    async def upload(self, path, name):
        async with self._client() as c:
            with open(path, "rb") as f:
                r = await c.post("/upload/image", files={"image": (name, f, "application/octet-stream")},
                                 data={"overwrite": "true"})
        if r.status_code != 200:
            raise ComfyError(f"ComfyUI refused the image: HTTP {r.status_code}")
        return r.json()["name"]

    async def run(self, workflow, timeout=600, poll=0.5):
        """Queues a workflow, waits for it, returns the bytes of its first output image."""
        async with self._client() as c:
            r = await c.post("/prompt", json={"prompt": workflow})
            if r.status_code != 200:
                raise ComfyError(_refusal(r))
            pid = r.json()["prompt_id"]
            for _ in range(int(timeout / poll)):
                h = (await c.get(f"/history/{pid}")).json().get(pid)
                if h:
                    st = h.get("status", {})
                    if st.get("status_str") == "error":
                        msgs = [m[1].get("exception_message", "") for m in st.get("messages", [])
                                if m[0] == "execution_error"]
                        raise ComfyError(f"ComfyUI stopped: {(msgs[0] if msgs else 'error').strip()[:300]}")
                    for out in h.get("outputs", {}).values():
                        for im in out.get("images", []):
                            v = await c.get("/view", params={"filename": im["filename"],
                                                             "subfolder": im.get("subfolder", ""),
                                                             "type": im.get("type", "output")})
                            if v.status_code != 200:
                                raise ComfyError(f"cannot read the result: HTTP {v.status_code}")
                            return v.content
                    if st.get("completed"):
                        raise ComfyError("ComfyUI finished without an image")
                await asyncio.sleep(poll)
        raise ComfyError(f"no image after {timeout} s")


def _refusal(r):
    """ComfyUI's 400 explains which node and which input it refused: keep that, briefly."""
    try:
        d = r.json()
    except ValueError:
        return f"ComfyUI refused the workflow: HTTP {r.status_code}"
    parts = [d.get("error", {}).get("message", "")]
    for node in (d.get("node_errors") or {}).values():
        for e in node.get("errors", []):
            parts.append(f"{node.get('class_type', '')}: {e.get('details') or e.get('message')}")
    return "ComfyUI refused the workflow: " + "; ".join(p for p in parts if p)[:300]
