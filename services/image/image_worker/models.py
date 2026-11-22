"""The image models: where they come from, what they must hash to, and downloading the missing ones.

Every model comes from its official source and is checked against its sha256 before it is used: a
truncated or swapped file is deleted, never loaded. Licenses: docs/licenses.md.
"""
import hashlib
import os
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class Model:
    folder: str
    file: str
    url: str
    sha256: str
    size: int
    license: str


CHECKPOINT = Model(
    "checkpoints", "sd_xl_base_1.0.safetensors",
    "https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/resolve/"
    "462165984030d82259a11f4367a4eed129e94a7b/sd_xl_base_1.0.safetensors",
    "31e35c80fc4829d14f90153f4c74cd59c90b779f6afe05a74cd6120b893f7e5b", 6938078334, "CreativeML OpenRAIL++-M")
LIGHTNING = Model(
    "loras", "sdxl_lightning_4step_lora.safetensors",
    "https://huggingface.co/ByteDance/SDXL-Lightning/resolve/"
    "c9a24f48e1c025556787b0c58dd67a091ece2e44/sdxl_lightning_4step_lora.safetensors",
    "bf56cf2657efb15e465d81402ed481d1e11c4677e4bcce1bc11fe71ad8506b79", 393854592, "CreativeML OpenRAIL++-M")
REALESRGAN = Model(
    "upscale_models", "RealESRGAN_x4plus.pth",
    "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth",
    "4fa0d38905f75ac06eb49a7951b426670021be3018265fd191d2125df9d682f1", 67040989, "BSD-3-Clause")
BIREFNET = Model(
    "background_removal", "birefnet.safetensors",
    "https://huggingface.co/Comfy-Org/BiRefNet/resolve/"
    "884af709e9b795d89dafbf97f29c8d6a2386f5c6/background_removal/birefnet.safetensors",
    "9ab37426bf4de0567af6b5d21b16151357149139362e6e8992021b8ce356a154", 444473596, "MIT")

FOR_TASK = {"generate": [CHECKPOINT, LIGHTNING], "upscale": [REALESRGAN], "cutout": [BIREFNET]}


class ModelError(RuntimeError):
    pass


def path_of(root, m):
    return os.path.join(root, m.folder, m.file)


def missing(root, models):
    return [m for m in models if not os.path.isfile(path_of(root, m))]


async def ensure(root, models, progress=None, transport=None):
    """Downloads the models that are not there yet. progress(file, done_bytes, total_bytes) is awaited
    while downloading; the sha256 is checked before the file takes its final name."""
    for m in missing(root, models):
        dest = path_of(root, m)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        part = dest + ".part"
        h, done = hashlib.sha256(), 0
        try:
            async with httpx.AsyncClient(transport=transport, follow_redirects=True,
                                         timeout=httpx.Timeout(60, read=300)) as c:
                async with c.stream("GET", m.url) as r:
                    if r.status_code != 200:
                        raise ModelError(f"download of {m.file} failed: HTTP {r.status_code}")
                    total = int(r.headers.get("content-length") or m.size)
                    with open(part, "wb") as f:
                        async for chunk in r.aiter_bytes(2**20):
                            f.write(chunk)
                            h.update(chunk)
                            done += len(chunk)
                            if progress:
                                await progress(m.file, done, total)
            if h.hexdigest() != m.sha256:
                raise ModelError(f"{m.file}: sha256 does not match the official file, download discarded")
            os.replace(part, dest)
        except httpx.HTTPError as e:
            raise ModelError(f"download of {m.file} failed: {e}") from None
        finally:
            if os.path.exists(part):
                os.remove(part)
