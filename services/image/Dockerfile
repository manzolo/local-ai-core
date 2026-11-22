# Image worker: ComfyUI on the GPU behind a small adapter (generate with SDXL + Lightning, upscale with
# RealESRGAN, cut out with BiRefNet). Compose profile `image`. Build context: the repository root.
# ComfyUI (GPL-3.0) is cloned at a fixed commit and run unmodified as a separate program; the adapter
# (MIT) talks to it over HTTP on 127.0.0.1 only. Models are not in the image: they download on first use
# into the volume at /models, checked against their sha256.
FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df
ENV DEBIAN_FRONTEND=noninteractive PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
# PyTorch first, with exactly these lines in every torch image (documents, speakers): Docker keeps one copy.
COPY requirements/torch.txt /tmp/torch.txt
RUN pip install --root-user-action=ignore -r /tmp/torch.txt
ARG DEBIAN_SNAPSHOT=20261121T000000Z
# Debian packages pinned to a snapshot date, so two builds install the same files.
RUN printf 'Types: deb\nURIs: http://snapshot.debian.org/archive/debian/%s\nSuites: bookworm bookworm-updates\nComponents: main\nSigned-By: /usr/share/keyrings/debian-archive-keyring.gpg\n\nTypes: deb\nURIs: http://snapshot.debian.org/archive/debian-security/%s\nSuites: bookworm-security\nComponents: main\nSigned-By: /usr/share/keyrings/debian-archive-keyring.gpg\n' \
        "$DEBIAN_SNAPSHOT" "$DEBIAN_SNAPSHOT" > /etc/apt/sources.list.d/debian.sources && \
    printf 'Acquire::Check-Valid-Until "false";\nAcquire::Retries "3";\n' > /etc/apt/apt.conf.d/99snapshot && \
    apt-get update && apt-get install -y --no-install-recommends git libgl1 libglib2.0-0 && \
    rm -rf /var/lib/apt/lists/* /var/cache/apt
ARG COMFYUI_TAG=v0.46.2
ARG COMFYUI_COMMIT=10cdd12609eb17e8a90f2a2dd811b1c82f6107e7
RUN git clone --depth 1 --branch "$COMFYUI_TAG" https://github.com/comfyanonymous/ComfyUI /opt/comfyui && \
    test "$(git -C /opt/comfyui rev-parse HEAD)" = "$COMFYUI_COMMIT" && rm -rf /opt/comfyui/.git
COPY requirements/image.txt /tmp/image.txt
RUN pip install --root-user-action=ignore -r /tmp/image.txt && \
    pip install --root-user-action=ignore -c /tmp/image.txt -r /opt/comfyui/requirements.txt
# The model folders live in the /models volume; ComfyUI's own empty folders show which ones it expects.
RUN mv /opt/comfyui/models /opt/comfyui/models.dist && ln -s /models /opt/comfyui/models
WORKDIR /srv
COPY services/image/image_worker ./image_worker
RUN useradd --uid 10001 --create-home worker && mkdir -p /data/uploads /models /data/comfy && \
    chown -R worker /data /models && chmod -R a+rwX /opt/comfyui
USER worker
ENV HOME=/tmp UPLOADS_DIR=/data/uploads MODELS_DIR=/models COMFY_DIR=/opt/comfyui COMFY_DATA=/data/comfy
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"
CMD ["uvicorn", "image_worker.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
