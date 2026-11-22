# Search worker: semantic search and grounded answers over a folder of Markdown notes.
# CPU only: the heavy lifting (embeddings, the LLM) happens in Ollama.
# Build context: the repository root (see docker-compose.yml).
FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /srv
COPY requirements/runtime.txt /tmp/runtime.txt
RUN pip install --root-user-action=ignore -r /tmp/runtime.txt
COPY services/search/search_worker ./search_worker
RUN useradd --uid 10001 --create-home worker && mkdir -p /data/index && chown -R worker /data
USER worker
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"
CMD ["uvicorn", "search_worker.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
