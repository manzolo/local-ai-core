"""The few Ollama calls the search worker needs: embeddings and a streamed chat."""
import json
import math

import httpx


class OllamaError(RuntimeError):
    pass


def normalize(v):
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


class Ollama:
    def __init__(self, base_url, keep_alive="10m", transport=None):
        self.base_url = base_url.rstrip("/")
        self.keep_alive = keep_alive
        self.transport = transport

    def _client(self, timeout):
        return httpx.AsyncClient(base_url=self.base_url, transport=self.transport, timeout=timeout)

    async def embed(self, model, texts):
        body = {"model": model, "input": texts, "truncate": True, "keep_alive": self.keep_alive}
        try:
            async with self._client(300) as c:
                r = await c.post("/api/embed", json=body)
        except httpx.HTTPError as e:
            raise OllamaError(f"Ollama not reachable at {self.base_url}: {e}") from None
        if r.status_code != 200:
            raise OllamaError(f"Ollama answered {r.status_code}: {r.text[:200]}")
        vecs = r.json().get("embeddings") or []
        if len(vecs) != len(texts):
            raise OllamaError(f"Ollama returned {len(vecs)} vectors for {len(texts)} texts")
        return [normalize(v) for v in vecs]

    async def chat_stream(self, model, system, prompt, options=None):
        """Yields the answer piece by piece; the last item is the final record (done=True)."""
        body = {"model": model, "stream": True, "think": False, "keep_alive": self.keep_alive,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                "options": options or {"temperature": 0.2, "num_ctx": 8192, "num_predict": 1200}}
        try:
            async with self._client(httpx.Timeout(300, connect=10)) as c:
                async with c.stream("POST", "/api/chat", json=body) as r:
                    if r.status_code != 200:
                        raise OllamaError(f"Ollama answered {r.status_code}: {(await r.aread())[:200]!r}")
                    async for line in r.aiter_lines():
                        if not line.strip():
                            continue
                        d = json.loads(line)
                        if "error" in d:
                            raise OllamaError(f"Ollama says: {d['error'][:200]}")
                        yield d
        except httpx.HTTPError as e:
            raise OllamaError(f"Ollama not reachable at {self.base_url}: {e}") from None
