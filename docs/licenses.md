# Licenses

[← back to the index](../README.md)

**In short:** the code in this repository is MIT. The repository contains only that code, invented
example data and screenshots of its own interface. Third-party software and models are **not in the
repository**: they are downloaded on your machine, from their official sources, when you build the images
or use a service for the first time, and each keeps its own license. Checked on 2026-11-22.

## 1. What this repository contains

| What | License |
|---|---|
| Code, scripts, configuration, documentation | [MIT](../LICENSE) |
| Example notes (`examples/notes`) and the example bill (`examples/documents`, and the PDFs made from it) | invented for this project, MIT like the rest |
| Test speech (`services/*/selftest/*.ogg`) | invented sentences spoken by two [Piper](https://github.com/OHF-Voice/piper1-gpl) voices trained on public-domain recordings ([LJ Speech](https://keithito.com/LJ-Speech-Dataset/), [LibriVox](https://librivox.org)); MIT like the rest |
| Screenshots (`docs/img`) | pictures of this project's own interface showing the example data, MIT |

No third-party code is copied into the repository.

## 2. Models, downloaded on first use

| Model | Service | License | Source |
|---|---|---|---|
| bge-m3 | search | MIT | [BAAI/bge-m3](https://huggingface.co/BAAI/bge-m3) |
| qwen3.5:9b | ask | Apache-2.0 | [Qwen/Qwen3.5-9B](https://huggingface.co/Qwen/Qwen3.5-9B), via [Ollama](https://ollama.com/library/qwen3.5) |
| docling-layout-heron | documents (page layout) | Apache-2.0 | [docling-project/docling-layout-heron](https://huggingface.co/docling-project/docling-layout-heron) |
| docling-models (TableFormer) | documents (tables) | CDLA-Permissive-2.0 and Apache-2.0 | [docling-project/docling-models](https://huggingface.co/docling-project/docling-models) |
| PP-OCRv6 (through RapidOCR) | documents (OCR of scans) | Apache-2.0 | [RapidOCR](https://github.com/RapidAI/RapidOCR), from [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) |
| Whisper large-v3 (CTranslate2 conversion) | transcribe | MIT | [Systran/faster-whisper-large-v3](https://huggingface.co/Systran/faster-whisper-large-v3) |
| spkrec-ecapa-voxceleb | speakers | Apache-2.0 | [speechbrain/spkrec-ecapa-voxceleb](https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb) |
| SDXL base 1.0 | images (generate) | **CreativeML OpenRAIL++-M**, with use restrictions | [stabilityai/stable-diffusion-xl-base-1.0](https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0) |
| SDXL-Lightning 4-step LoRA | images (generate) | **CreativeML OpenRAIL++-M**, with use restrictions | [ByteDance/SDXL-Lightning](https://huggingface.co/ByteDance/SDXL-Lightning) |
| RealESRGAN x4plus | images (upscale) | BSD-3-Clause | [xinntao/Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN/releases/tag/v0.1.0) |
| BiRefNet | images (cut out) | MIT | [Comfy-Org/BiRefNet](https://huggingface.co/Comfy-Org/BiRefNet), from [ZhengPeng7/BiRefNet](https://github.com/ZhengPeng7/BiRefNet) |

All of them allow commercial use. The two OpenRAIL++-M models add **use restrictions** that pass to
whoever uses them and to the pictures' use: no breaking the law, no harm to people, no deceiving with
generated content, and the others listed in the license (its Attachment A). The image service states it
in `/v1/services` and in the web page. If you set `OLLAMA_MODEL`, `EMBED_MODEL` or `IMAGE_CHECKPOINT` to
another model, check that model's license yourself.

## 3. Software, installed when the images are built

| Software | License | Where |
|---|---|---|
| [Ollama](https://github.com/ollama/ollama) (image `ollama/ollama`) | MIT | standalone stack |
| [Docling](https://github.com/docling-project/docling) | MIT | documents |
| [RapidOCR](https://github.com/RapidAI/RapidOCR) | Apache-2.0 | documents |
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper), [CTranslate2](https://github.com/OpenNMT/CTranslate2) | MIT | transcribe |
| [SpeechBrain](https://github.com/speechbrain/speechbrain), scikit-learn | Apache-2.0, BSD-3-Clause | speakers |
| FFmpeg (Debian package) | LGPL-2.1+ / GPL-2+ (Debian build) | transcribe, speakers: run as a separate program to decode audio |
| PyTorch | BSD-3-Clause | documents |
| **NVIDIA CUDA libraries** (cuBLAS, cuDNN, NCCL and the other `nvidia-*` wheels pulled in by PyTorch and CTranslate2) | **NVIDIA proprietary terms** | documents, transcribe, speakers |
| FastAPI, Starlette, Pydantic, httpx, Uvicorn and the other Python packages | MIT, BSD, Apache-2.0, PSF; certifi and tqdm under MPL-2.0 | all images |
| [Swagger UI](https://github.com/swagger-api/swagger-ui) (served at `/docs`) | Apache-2.0, its `LICENSE` and `NOTICE` kept next to it in the image | gateway |
| Python and Debian (base image `python:3.12-slim-bookworm`) | PSF, and the licenses of the Debian packages | all images |
| [Playwright](https://github.com/microsoft/playwright-python) (headless Chromium) | Apache-2.0 | only `scripts/screenshots.sh` and `scripts/make-fixtures.sh` |
| [ComfyUI](https://github.com/comfyanonymous/ComfyUI) v0.46.2 | **GPL-3.0** | image: cloned at a fixed commit when the image is built, run **unmodified as a separate program** and spoken to over HTTP; no ComfyUI file is in this repository |
| [Piper](https://github.com/OHF-Voice/piper1-gpl) | GPL-3.0 | only `scripts/make-audio-fixtures.sh`, never in a service image; the audio it makes is not covered by its license |

**Every Python package, with its version and license:** [licenses-python.md](licenses-python.md)
(generated from PyPI by `scripts/licenses.py`). The only GPL ones are ComfyUI's own packages
(`comfy-aimdo`, `comfyui-embedded-docs`, `comfyui_frontend_package`), inside the **image** image only, used
unmodified by ComfyUI; none is AGPL, and none is imported by this repository's code.

## 4. If you publish the images

This project publishes source code only, never built images. Building them on your machine is how the
software above is meant to be used. If you push a built image to a registry, you redistribute what is
inside it:

- the **documents**, **transcriber** and **speakers** images contain NVIDIA's CUDA libraries:
  redistribution is governed by NVIDIA's license terms for those components, so check them first, or
  publish only the images without CUDA (gateway, search);
- the **transcriber** and **speakers** images contain FFmpeg from Debian, whose build includes GPL
  components: publishing them means offering the corresponding source, as Debian does;
- the **image** image contains ComfyUI (GPL-3.0): publishing it means offering ComfyUI's source (its
  public repository at the commit in the Dockerfile) and keeping its license;
- keep the license files of the components (Apache-2.0 asks for its `NOTICE` files too);
- MPL-2.0 packages (certifi, tqdm) are used unmodified: their source stays available on PyPI.

## 5. Services still to come

The roadmap includes XTTS-v2 for voice, which is under the **Coqui Public Model License: non-commercial
use only**. It will live in its own profile, off by default, and its license will be shown in the README,
in `/v1/services` and in the web page.
