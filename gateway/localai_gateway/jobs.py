"""Asynchronous jobs and the GPU queue.

A job runs in the background and records what happens as numbered events (status, progress, tokens,
checks, the result). Clients poll GET /v1/jobs/{id} or follow the events as Server-Sent Events, and a
client that reconnects asks only for the events after the last one it saw.

GPU jobs take one lock: one heavy job at a time, so 12 GB of VRAM never has to hold a speech model, a
voice model and an LLM at once. Jobs are kept in memory (the last KEEP of them): a gateway restart
forgets them, by design for now.
"""
import asyncio
import time
import uuid
from collections import OrderedDict

KEEP = 200
TERMINAL = {"done", "error"}


class JobFailed(RuntimeError):
    """A job failure whose message is meant for the user (no traceback)."""


class Job:
    def __init__(self, kind, params, gpu):
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.params = params
        self.gpu = gpu
        self.status = "queued"
        self.created = time.time()
        self.started = None
        self.finished = None
        self.progress = None
        self.result = None
        self.error = None
        self.events = []
        self.file = None  # a result file (an image) served at /v1/jobs/{id}/file
        self._changed = asyncio.Condition()

    async def emit(self, type_, data=None):
        async with self._changed:
            self.events.append({"seq": len(self.events) + 1, "type": type_, "data": data})
            self._changed.notify_all()

    async def set_progress(self, done, total, message=None):
        self.progress = {"done": done, "total": total, "message": message}
        await self.emit("progress", self.progress)

    def public(self):
        return {"id": self.id, "kind": self.kind, "status": self.status, "params": self.params,
                "created": self.created, "started": self.started, "finished": self.finished,
                "progress": self.progress, "result": self.result, "error": self.error,
                "events": len(self.events)}

    async def follow(self, after=0):
        """Events after seq `after`, then new ones as they come, until the job ends."""
        i = after
        while True:
            async with self._changed:
                while i >= len(self.events) and self.status not in TERMINAL:
                    await self._changed.wait()
                new = self.events[i:]
            for ev in new:
                yield ev
            i += len(new)
            if self.status in TERMINAL and i >= len(self.events):
                return

    async def wait(self):
        async for _ in self.follow(len(self.events)):
            pass


class JobStore:
    def __init__(self, on_evict=None):
        self.on_evict = on_evict  # called with a job that leaves memory: its files go with it
        self.jobs = OrderedDict()
        self.gpu = asyncio.Lock()
        self.waiting = 0
        self.running = None
        self._tasks = set()

    def submit(self, kind, params, fn, gpu=True, on_done=None):
        job = Job(kind, params, gpu)
        self.jobs[job.id] = job
        while len(self.jobs) > KEEP:
            old_id = next(iter(self.jobs))
            if self.jobs[old_id].status not in TERMINAL:
                break
            old = self.jobs.pop(old_id)
            if self.on_evict:
                self.on_evict(old)
        task = asyncio.create_task(self._run(job, fn, on_done))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job

    def get(self, job_id):
        return self.jobs.get(job_id)

    def queue(self):
        return {"running": self.running.public() if self.running else None, "waiting": self.waiting}

    async def _run(self, job, fn, on_done):
        if job.gpu:
            self.waiting += 1
            try:
                async with self.gpu:
                    self.waiting -= 1
                    self.running = job
                    try:
                        await self._exec(job, fn)
                    finally:
                        self.running = None
            except BaseException:
                if job.status == "queued":
                    self.waiting -= 1
                raise
        else:
            await self._exec(job, fn)
        if on_done:
            await on_done(job)

    async def _exec(self, job, fn):
        job.status, job.started = "running", time.time()
        await job.emit("status", "running")
        try:
            job.result = await fn(job)
            job.status = "done"
            await job.emit("result", job.result)
        except JobFailed as e:
            job.status, job.error = "error", str(e)
            await job.emit("error", job.error)
        except Exception as e:  # noqa: BLE001 — a job never takes the gateway down
            job.status, job.error = "error", f"{type(e).__name__}: {e}"
            await job.emit("error", job.error)
        finally:
            job.finished = time.time()
            await job.emit("status", job.status)

    async def close(self):
        for t in list(self._tasks):
            t.cancel()
