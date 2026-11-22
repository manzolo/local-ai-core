# Licenses

[← back to the index](../README.md)

The code of this repository is under the [MIT license](../LICENSE).

**Models are not part of the repository and are not redistributed**: Ollama downloads them from its
registry at the first start, and each one keeps its own license. Check that it fits your use.

| Model | Used by | License | Source |
|---|---|---|---|
| bge-m3 | search | MIT | [BAAI/bge-m3](https://huggingface.co/BAAI/bge-m3) |
| qwen3.5:9b | ask | Apache-2.0 | [ollama.com/library/qwen3.5](https://ollama.com/library/qwen3.5) |

Third-party software inside the images:

| Software | License |
|---|---|
| Ollama | MIT |
| FastAPI, Starlette, Pydantic, httpx, Uvicorn | MIT / BSD-3-Clause |
| Swagger UI (served at `/docs`) | Apache-2.0 |
| Python | PSF License |

> Status: the licenses above are the ones the model and project pages declare. They are checked one by
> one again before every release.
