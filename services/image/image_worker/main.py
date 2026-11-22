"""Image worker: generate images from text, upscale and cut out photos, with ComfyUI on the GPU.

Internal API, on the compose network only:
  GET  /health      liveness
  GET  /info        the service, its models, VRAM estimate, licenses, whether ComfyUI is running
  POST /generate    {"prompt", "negative", "width", "height", "steps", "seed", "out"}   (NDJSON events)
  POST /upscale     {"path", "scale": 2|4, "out"}                                         (NDJSON events)
  POST /cutout      {"path", "background": ""|white|black|#rrggbb, "out"}               (NDJSON events)
  POST /unload      stops ComfyUI now: all of its VRAM goes back
  POST /selftest    known inputs → expected images, plus introduced errors (NDJSON)

Missing models download on first use (progress events), checked against their sha256. ComfyUI starts on
the first job and stops after IDLE_UNLOAD_MINUTES without work.
"""
import asyncio
import contextlib
import io
import json
import os
import shutil
import tempfile
import time

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import models, workflows
from .comfy import ComfyClient, ComfyError, ComfyProcess

VERSION = "0.1.0"
UPLOADS = os.environ.get("UPLOADS_DIR", "/data/uploads")
MODELS_DIR = os.environ.get("MODELS_DIR", "/models")
IDLE_MINUTES = int(os.environ.get("IDLE_UNLOAD_MINUTES", "10"))
CHECKPOINT = os.environ.get("IMAGE_CHECKPOINT", "") or models.CHECKPOINT.file
NEGATIVE = "blurry, deformed, low quality, text, watermark"
BACKGROUNDS = {"white": (255, 255, 255), "black": (0, 0, 0)}


class ImageError(RuntimeError):
    pass


@contextlib.asynccontextmanager
async def lifespan(app):
    async def idle_stopper():
        while True:
            await asyncio.sleep(30)
            if IDLE_MINUTES and time.time() - app.state.last_used > IDLE_MINUTES * 60 \
                    and not app.state.lock.locked():
                app.state.process.stop()
    task = asyncio.create_task(idle_stopper())
    yield
    task.cancel()
    app.state.process.stop()


app = FastAPI(title="local-ai-core image worker", version=VERSION, lifespan=lifespan)
app.state.process = ComfyProcess(os.environ.get("COMFY_DIR", "/opt/comfyui"),
                                 os.environ.get("COMFY_DATA", "/data/comfy"))
app.state.comfy_transport = None     # tests put a fake ComfyUI here
app.state.download_transport = None  # and a fake model server here
app.state.lock = asyncio.Lock()
app.state.last_used = time.time()
app.state.last_task = None  # the kind of the last job: a different one first frees ComfyUI's models


def comfy():
    return ComfyClient(app.state.process.url, transport=app.state.comfy_transport)


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/info")
async def info():
    custom = CHECKPOINT != models.CHECKPOINT.file
    return {
        "worker": "image", "version": VERSION, "profile": "image",
        "services": [{
            "name": "images", "title": "Images",
            "description": "Pictures from a description (SDXL with the Lightning LoRA, 4 steps), photos upscaled "
                           "×2 or ×4 (RealESRGAN) and cut out on a transparent background (BiRefNet), with ComfyUI.",
            "model": f"ComfyUI v0.46.2 · {CHECKPOINT} + SDXL-Lightning · RealESRGAN · BiRefNet",
            "ollama": False, "vram_gb": 8,
            "license": ("checkpoint: see its own license" if custom else "CreativeML OpenRAIL++-M")
                       + " · BSD-3-Clause · MIT; ComfyUI GPL-3.0",
            "license_url": "https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0",
            "use_restrictions": "OpenRAIL++-M: no use that breaks the law or harms people; see the license",
            "loaded": app.state.process.running,
            "endpoints": ["/v1/images/generate", "/v1/images/upscale", "/v1/images/cutout"],
        }],
    }


def inside_uploads(p):
    real, root = os.path.realpath(p), os.path.realpath(UPLOADS)
    return real == root or real.startswith(root + os.sep)


def needed(task):
    if task != "generate":
        return models.FOR_TASK[task]
    if CHECKPOINT == models.CHECKPOINT.file:
        return models.FOR_TASK["generate"]
    path = os.path.join(MODELS_DIR, "checkpoints", CHECKPOINT)
    if not os.path.isfile(path):
        raise ImageError(f"IMAGE_CHECKPOINT {CHECKPOINT!r} is not in the models volume (checkpoints/): "
                         "copy it there, or unset IMAGE_CHECKPOINT to use SDXL base")
    return [models.LIGHTNING]


async def run_task(task, workflow_of, out, src=None, post=None):
    """Async generator: progress events, then {"type": "result", ...}. workflow_of(image_name) builds the
    workflow once the input photo (if any) is in ComfyUI."""
    async with app.state.lock:
        t0 = time.time()
        try:
            q: asyncio.Queue = asyncio.Queue()

            async def progress(name, done, total):
                await q.put({"type": "progress", "message": f"downloading {name}", "done": done, "total": total})

            dl = asyncio.create_task(models.ensure(MODELS_DIR, needed(task), progress, app.state.download_transport))
            while not dl.done() or not q.empty():
                try:
                    yield await asyncio.wait_for(q.get(), 0.5)
                except TimeoutError:
                    pass
            dl.result()
            if not app.state.process.running and app.state.comfy_transport is None:
                yield {"type": "progress", "message": "starting ComfyUI"}
                await app.state.process.start(MODELS_DIR)
            yield {"type": "progress", "message": task}
            c = comfy()
            if app.state.last_task not in (None, task):
                await c.free()  # another kind of job: its models out before this one's come in
            app.state.last_task = task
            name = await c.upload(src, f"lac-{os.getpid()}-{int(t0 * 1000)}{os.path.splitext(src)[1]}") if src else None
            png = await c.run(workflow_of(name))
        except (models.ModelError, ComfyError) as e:
            raise ImageError(str(e)) from None
        finally:
            app.state.last_used = time.time()
    from PIL import Image
    img = Image.open(io.BytesIO(png))
    if post:
        img = post(img)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    img.save(out, "PNG")
    yield {"type": "result", "width": img.width, "height": img.height, "mode": img.mode,
           "secs": round(time.time() - t0, 1)}


def ndjson(gen):
    async def body():
        try:
            async for ev in gen:
                yield json.dumps(ev) + "\n"
        except ImageError as e:
            yield json.dumps({"type": "error", "message": str(e)}) + "\n"
    return StreamingResponse(body(), media_type="application/x-ndjson")


class GenerateReq(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)
    negative: str = Field(NEGATIVE, max_length=2000)
    width: int = Field(1024, ge=512, le=1536, multiple_of=8)
    height: int = Field(1024, ge=512, le=1536, multiple_of=8)
    steps: int = Field(4, ge=1, le=8)
    seed: int = Field(0, ge=0, le=2**32 - 1)
    out: str


def generate_events(r: GenerateReq):
    async def gen():
        async for ev in run_task("generate", lambda _: workflows.generate(
                r.prompt, r.negative, r.width, r.height, r.steps, r.seed, CHECKPOINT, models.LIGHTNING.file), r.out):
            if ev["type"] == "result":
                ev["seed"] = r.seed
            yield ev
    return gen()


@app.post("/generate")
async def generate(r: GenerateReq):
    if not inside_uploads(r.out):
        raise HTTPException(400, "the output must be inside the uploads volume")
    return ndjson(generate_events(r))


class UpscaleReq(BaseModel):
    path: str
    scale: int = Field(4)
    out: str


@app.post("/upscale")
async def upscale(r: UpscaleReq):
    check_paths(r.path, r.out)
    if r.scale not in (2, 4):
        raise HTTPException(422, "scale must be 2 or 4")
    return ndjson(run_task("upscale", lambda name: workflows.upscale(name, r.scale, models.REALESRGAN.file),
                           r.out, src=r.path))


class CutoutReq(BaseModel):
    path: str
    background: str = Field("", pattern=r"^(|white|black|#[0-9a-fA-F]{6})$")
    out: str


def fill(background):
    """A solid background behind a cut-out subject; none keeps the transparency."""
    if not background:
        return None
    rgb = BACKGROUNDS.get(background) or tuple(int(background[i:i + 2], 16) for i in (1, 3, 5))

    def post(img):
        from PIL import Image
        img = img.convert("RGBA")
        base = Image.new("RGBA", img.size, rgb + (255,))
        return Image.alpha_composite(base, img).convert("RGB")
    return post


@app.post("/cutout")
async def cutout(r: CutoutReq):
    check_paths(r.path, r.out)
    return ndjson(run_task("cutout", lambda name: workflows.cutout(name, models.BIREFNET.file), r.out,
                           src=r.path, post=fill(r.background)))


def check_paths(src, out):
    if not (inside_uploads(src) and inside_uploads(out)):
        raise HTTPException(400, "paths must be inside the uploads volume")
    if not os.path.isfile(src):
        raise HTTPException(404, "no such file")


@app.post("/unload")
async def unload():
    async with app.state.lock:
        app.state.last_task = None
        return {"unloaded": app.state.process.stop()}


# ------------------------------------------------------------------------------------------ self-test

def synthetic_photo(path):
    """64×64: a red disk on a blue background. Made here, so the test needs no picture of anyone."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (64, 64), (30, 58, 138))
    ImageDraw.Draw(img).ellipse((12, 12, 52, 52), fill=(220, 38, 38))
    img.save(path)


def alpha_at(path, xy):
    from PIL import Image
    return Image.open(path).convert("RGBA").getpixel(xy)[3]


async def drain(gen):
    """Runs a task generator: progress events are passed on, the result is returned at the end."""
    result = None
    async for ev in gen:
        if ev["type"] == "result":
            result = ev
        else:
            yield ev
    yield {"type": "_result", "result": result}


async def selftest_checks():
    """Generate 512×512 with a fixed seed; a checkpoint that does not exist must be refused; upscale a
    synthetic 64×64 photo ×4; cut it out: the disk opaque, the corner transparent; the same cut-out without
    inverting the mask must fail that check (the easy mistake)."""
    def check(name, ok, detail):
        return {"type": "check", "name": name, "ok": bool(ok), "detail": detail}

    tmp = tempfile.mkdtemp(prefix="selftest-", dir=UPLOADS if os.path.isdir(UPLOADS) else None)
    try:
        photo = os.path.join(tmp, "photo.png")
        synthetic_photo(photo)
        out = os.path.join(tmp, "gen.png")
        req = GenerateReq(prompt="a red apple on a wooden table, photo", width=512, height=512, steps=4, seed=42,
                          out=out)
        try:
            res = None
            async for ev in drain(generate_events(req)):
                if ev["type"] == "_result":
                    res = ev["result"]
                else:
                    yield ev
            from PIL import Image, ImageStat
            spread = max(ImageStat.Stat(Image.open(out).convert("L")).stddev)
            yield check("generate 512×512, seed 42", (res["width"], res["height"]) == (512, 512) and spread > 5,
                        f"{res['width']}×{res['height']}, contrast {spread:.0f} (a flat picture would be ~0), "
                        f"{res['secs']} s")
        except ImageError as e:
            yield check("generate 512×512, seed 42", False, str(e))

        try:
            await comfy().run(workflows.generate("x", "y", 512, 512, 4, 1, "does-not-exist.safetensors",
                                                 models.LIGHTNING.file))
            yield check("introduced error: a checkpoint that does not exist", False, "the workflow was accepted")
        except ComfyError as e:
            yield check("introduced error: a checkpoint that does not exist", "refused" in str(e), str(e)[:120])

        up = os.path.join(tmp, "x4.png")
        try:
            async for ev in drain(run_task("upscale", lambda n: workflows.upscale(n, 4, models.REALESRGAN.file),
                                           up, src=photo)):
                if ev["type"] != "_result":
                    yield ev
            from PIL import Image
            size = Image.open(up).size
            yield check("upscale ×4: 64×64 → 256×256", size == (256, 256), f"{size[0]}×{size[1]}")
        except ImageError as e:
            yield check("upscale ×4: 64×64 → 256×256", False, str(e))

        for invert in (True, False):
            cut = os.path.join(tmp, f"cut-{invert}.png")
            name = ("cut out: subject opaque, background transparent" if invert
                    else "introduced error: the mask not inverted")
            try:
                async for ev in drain(run_task("cutout", lambda n, inv=invert: workflows.cutout(
                        n, models.BIREFNET.file, invert=inv), cut, src=photo)):
                    if ev["type"] != "_result":
                        yield ev
                mid, corner = alpha_at(cut, (32, 32)), alpha_at(cut, (2, 2))
                right = mid >= 200 and corner <= 50
                yield check(name, right if invert else not right,
                            f"alpha at the centre {mid}, in the corner {corner}"
                            + ("" if invert else ": caught, the subject would vanish"))
            except ImageError as e:
                yield check(name, False, str(e))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@app.post("/selftest")
async def selftest():
    async def body():
        ok = True
        async for c in selftest_checks():
            if c["type"] == "check":
                ok = ok and c["ok"]
            yield json.dumps(c, ensure_ascii=False) + "\n"
        yield json.dumps({"type": "result", "ok": ok}) + "\n"
    return StreamingResponse(body(), media_type="application/x-ndjson")
