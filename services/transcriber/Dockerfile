# Transcriber worker: faster-whisper on CTranslate2 (CUDA 12) on the GPU. Compose profile `transcribe`.
# Build context: the repository root. No PyTorch: CUDA comes as cuBLAS and cuDNN wheels; the model
# downloads on first use into the volume at /models (HF_HOME).
FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df
ENV DEBIAN_FRONTEND=noninteractive PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
ARG DEBIAN_SNAPSHOT=20261121T000000Z
RUN printf 'Types: deb\nURIs: http://snapshot.debian.org/archive/debian/%s\nSuites: bookworm bookworm-updates\nComponents: main\nSigned-By: /usr/share/keyrings/debian-archive-keyring.gpg\n\nTypes: deb\nURIs: http://snapshot.debian.org/archive/debian-security/%s\nSuites: bookworm-security\nComponents: main\nSigned-By: /usr/share/keyrings/debian-archive-keyring.gpg\n' \
        "$DEBIAN_SNAPSHOT" "$DEBIAN_SNAPSHOT" > /etc/apt/sources.list.d/debian.sources && \
    printf 'Acquire::Check-Valid-Until "false";\nAcquire::Retries "3";\n' > /etc/apt/apt.conf.d/99snapshot && \
    apt-get update && apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/* /var/cache/apt
COPY requirements/transcriber.txt /tmp/transcriber.txt
RUN pip install --root-user-action=ignore -r /tmp/transcriber.txt
# CTranslate2 looks for cuBLAS and cuDNN in the system: point it at the ones from the wheels
ENV LD_LIBRARY_PATH=/usr/local/lib/python3.12/site-packages/nvidia/cublas/lib:/usr/local/lib/python3.12/site-packages/nvidia/cudnn/lib
WORKDIR /srv
COPY services/transcriber/transcriber_worker ./transcriber_worker
RUN useradd --uid 10001 --create-home worker && mkdir -p /data/uploads /models && chown -R worker /data /models
USER worker
ENV HF_HOME=/models HOME=/tmp UPLOADS_DIR=/data/uploads
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"
CMD ["uvicorn", "transcriber_worker.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
