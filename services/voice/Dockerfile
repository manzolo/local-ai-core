# Voice worker: XTTS-v2 (coqui-tts) on the GPU. Compose profile `voice`, off by default.
# XTTS-v2 is under the Coqui Public Model License: NON-COMMERCIAL use only. The model is not in the image:
# it downloads on first use into /models, and only after the license is accepted (XTTS_ACCEPT_CPML=yes).
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
    apt-get update && apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/* /var/cache/apt
COPY requirements/voice.txt /tmp/voice.txt
RUN pip install --root-user-action=ignore -r /tmp/voice.txt
WORKDIR /srv
COPY services/voice/voice_worker ./voice_worker
RUN useradd --uid 10001 --create-home worker && mkdir -p /data/uploads /models && chown -R worker /data /models
USER worker
# TTS_HOME: where coqui-tts keeps XTTS-v2 (the models volume). COQUI_TOS_AGREED is NOT set here: the worker
# sets it only when the user has accepted the non-commercial license with XTTS_ACCEPT_CPML=yes.
ENV TTS_HOME=/models HOME=/tmp UPLOADS_DIR=/data/uploads OMP_NUM_THREADS=4
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"
CMD ["uvicorn", "voice_worker.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
