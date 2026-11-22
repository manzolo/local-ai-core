# Control worker: builds, starts and stops the optional services for the web page. Profile `control`,
# off by default. The only container with access to Docker: kept small, closed and read-only.
# Build context: the repository root. The Docker client and Compose are official static binaries at fixed
# versions, checked against their sha256 (the Docker daemon inside the archive is not kept).
ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df

FROM ${PYTHON_IMAGE} AS tools
ARG DOCKER_VERSION=29.9.2
ARG DOCKER_SHA256=9ac225999fd5e863f7694c728ef23d480616b1e5639328fa309befd4243d69eb
ARG COMPOSE_VERSION=v5.6.1
ARG COMPOSE_SHA256=47737f4236e6d3ccea7291995203f8f018058783b4c3a9469119811a0b5db8c0
RUN python - <<'PY'
import hashlib, io, os, tarfile, urllib.request
def get(url, sha):
    data = urllib.request.urlopen(url, timeout=120).read()
    got = hashlib.sha256(data).hexdigest()
    if got != sha:
        raise SystemExit(f"checksum mismatch for {url}: {got}")
    return data
v, cv = os.environ["DOCKER_VERSION"], os.environ["COMPOSE_VERSION"]
t = tarfile.open(fileobj=io.BytesIO(get(f"https://download.docker.com/linux/static/stable/x86_64/docker-{v}.tgz",
                                        os.environ["DOCKER_SHA256"])))
t.extract("docker/docker", "/tmp", filter="data")
os.makedirs("/out/cli-plugins", exist_ok=True)
os.replace("/tmp/docker/docker", "/out/docker")
with open("/out/cli-plugins/docker-compose", "wb") as f:
    f.write(get(f"https://github.com/docker/compose/releases/download/{cv}/docker-compose-linux-x86_64",
                os.environ["COMPOSE_SHA256"]))
for p in ("/out/docker", "/out/cli-plugins/docker-compose"):
    os.chmod(p, 0o755)
PY

FROM ${PYTHON_IMAGE}
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements/runtime.txt /tmp/runtime.txt
RUN pip install --root-user-action=ignore -r /tmp/runtime.txt
COPY --from=tools /out/docker /usr/local/bin/docker
COPY --from=tools /out/cli-plugins/docker-compose /usr/local/lib/docker/cli-plugins/docker-compose
WORKDIR /srv
COPY services/control/control_worker ./control_worker
ENV DOCKER_CONFIG=/tmp/docker HOME=/tmp
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"
CMD ["uvicorn", "control_worker.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
