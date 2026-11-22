# Reader worker (handwriting): light, CPU only; the reading is done by a vision model on Ollama.
# Compose profile `handwriting`. Build context: the repository root.
FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /srv
COPY requirements/runtime.txt requirements/reader.txt /tmp/
RUN pip install --root-user-action=ignore -r /tmp/reader.txt
COPY services/reader/reader_worker ./reader_worker
RUN useradd --uid 10001 --create-home worker && mkdir -p /data/uploads && chown -R worker /data
USER worker
ENV UPLOADS_DIR=/data/uploads
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"
CMD ["uvicorn", "reader_worker.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
