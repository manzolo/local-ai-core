# Documents worker: Docling (layout, TableFormer, RapidOCR on torch) on the GPU. Compose profile `docs`.
# Build context: the repository root. The models are not in the image: they download on first use into
# the volume mounted at /models (HF_HOME), RapidOCR's into /models/rapidocr.
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
    apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 && \
    rm -rf /var/lib/apt/lists/* /var/cache/apt
COPY requirements/documents.txt /tmp/documents.txt
RUN pip install --root-user-action=ignore -r /tmp/documents.txt
# RapidOCR downloads its models inside its own package: send them to the models volume instead
RUN d=/usr/local/lib/python3.12/site-packages/rapidocr/models && rm -rf "$d" && ln -s /models/rapidocr "$d"
WORKDIR /srv
COPY services/documents/documents_worker ./documents_worker
RUN useradd --uid 10001 --create-home worker && mkdir -p /data/uploads /models/rapidocr && chown -R worker /data /models
USER worker
ENV HF_HOME=/models HOME=/tmp OMP_NUM_THREADS=4 UPLOADS_DIR=/data/uploads
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"
CMD ["uvicorn", "documents_worker.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
