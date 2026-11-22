# Speech for the tests of the transcription services, synthesised with Piper (make audio-fixtures).
# Only this generator uses Piper (GPL-3.0): it is not part of any service image.
FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df
ARG DEBIAN_SNAPSHOT=20261121T000000Z
RUN printf 'Types: deb\nURIs: http://snapshot.debian.org/archive/debian/%s\nSuites: bookworm bookworm-updates\nComponents: main\nSigned-By: /usr/share/keyrings/debian-archive-keyring.gpg\n' \
        "$DEBIAN_SNAPSHOT" > /etc/apt/sources.list.d/debian.sources && \
    printf 'Acquire::Check-Valid-Until "false";\nAcquire::Retries "3";\n' > /etc/apt/apt.conf.d/99snapshot && \
    apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir --root-user-action=ignore piper-tts==1.8.0
COPY make_audio.py /make_audio.py
ENTRYPOINT ["python", "/make_audio.py"]
