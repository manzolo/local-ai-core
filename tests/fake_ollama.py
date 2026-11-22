"""A fake Ollama for the CPU-only tests: an httpx transport, no network, no GPU.

Embeddings are a bag of words hashed into 64 dimensions, so a query finds the notes that share its
words: enough to test the plumbing (the real meaning-based search is checked by `make collaudo`).
The chat answers whatever `reply` holds, word by word, and counts the calls.
"""
import hashlib
import json
import re

import httpx

DIM = 64


def embed_one(text):
    v = [0.0] * DIM
    for w in re.findall(r"[a-z0-9]{3,}", text.lower()):
        w = w[:-1] if w.endswith("s") else w
        v[int(hashlib.md5(w.encode()).hexdigest(), 16) % DIM] += 1.0
    return v


class FakeOllama:
    def __init__(self, models=("bge-m3:latest", "qwen3.5:9b"), reply="The invoice was 180 euros [1]."):
        self.models = list(models)
        self.reply = reply
        self.chat_calls = 0
        self.pulled = []
        self.transport = httpx.MockTransport(self.handle)

    def handle(self, request):
        path = request.url.path
        if path == "/api/version":
            return httpx.Response(200, json={"version": "0.0.0-fake"})
        if path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": m} for m in self.models]})
        if path == "/api/ps":
            return httpx.Response(200, json={"models": [{"name": "qwen3.5:9b", "size_vram": 7 * 2**30,
                                                         "expires_at": "2026-11-22T12:00:00Z"}]})
        if path == "/api/embed":
            body = json.loads(request.content)
            return httpx.Response(200, json={"embeddings": [embed_one(t) for t in body["input"]]})
        if path == "/api/pull":
            model = json.loads(request.content)["model"]
            self.pulled.append(model)
            self.models.append(model if ":" in model else model + ":latest")
            lines = [{"status": "pulling a", "digest": "sha256:a", "completed": 50, "total": 100},
                     {"status": "pulling b", "digest": "sha256:b", "completed": 300, "total": 300},
                     {"status": "pulling a", "digest": "sha256:a", "completed": 100, "total": 100},
                     {"status": "success"}]
            return httpx.Response(200, content="".join(json.dumps(x) + "\n" for x in lines))
        if path == "/api/chat":
            self.chat_calls += 1
            words = self.reply.split(" ")
            lines = [{"message": {"content": w + (" " if i < len(words) - 1 else "")}, "done": False}
                     for i, w in enumerate(words)]
            lines.append({"message": {"content": ""}, "done": True, "done_reason": "stop"})
            return httpx.Response(200, content="".join(json.dumps(x) + "\n" for x in lines))
        return httpx.Response(404, json={"error": f"fake ollama: no {path}"})
