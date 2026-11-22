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

All of them allow commercial use. If you set `OLLAMA_MODEL` or `EMBED_MODEL` to another model, check
that model's license yourself.

## 3. Software, installed when the images are built

| Software | License | Where |
|---|---|---|
| [Ollama](https://github.com/ollama/ollama) (image `ollama/ollama`) | MIT | standalone stack |
| [Docling](https://github.com/docling-project/docling) | MIT | documents |
| [RapidOCR](https://github.com/RapidAI/RapidOCR) | Apache-2.0 | documents |
| PyTorch | BSD-3-Clause | documents |
| **NVIDIA CUDA libraries** (cuBLAS, cuDNN, NCCL and the other `nvidia-*` wheels pulled in by PyTorch) | **NVIDIA proprietary terms** | documents |
| FastAPI, Starlette, Pydantic, httpx, Uvicorn and the other Python packages | MIT, BSD, Apache-2.0, PSF; certifi and tqdm under MPL-2.0 | all images |
| [Swagger UI](https://github.com/swagger-api/swagger-ui) (served at `/docs`) | Apache-2.0, its `LICENSE` and `NOTICE` kept next to it in the image | gateway |
| Python and Debian (base image `python:3.12-slim-bookworm`) | PSF, and the licenses of the Debian packages | all images |
| [Playwright](https://github.com/microsoft/playwright-python) (headless Chromium) | Apache-2.0 | only `scripts/screenshots.sh` and `scripts/make-fixtures.sh` |

**Every Python package, with its version and license:** [licenses-python.md](licenses-python.md)
(generated from PyPI by `scripts/licenses.py`). None is GPL or AGPL.

## 4. If you publish the images

This project publishes source code only, never built images. Building them on your machine is how the
software above is meant to be used. If you push a built image to a registry, you redistribute what is
inside it:

- the **documents** image contains NVIDIA's CUDA libraries: redistribution is governed by NVIDIA's
  license terms for those components, so check them first, or publish only the images without CUDA
  (gateway, search);
- keep the license files of the components (Apache-2.0 asks for its `NOTICE` files too);
- MPL-2.0 packages (certifi, tqdm) are used unmodified: their source stays available on PyPI.

## 5. Services still to come

The roadmap includes XTTS-v2 for voice, which is under the **Coqui Public Model License: non-commercial
use only**. It will live in its own profile, off by default, and its license will be shown in the README,
in `/v1/services` and in the web page. Image models (SDXL checkpoints, LoRA) will have their licenses
checked one by one before they are added.
