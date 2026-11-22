# Gateway: the /v1 API, its OpenAPI docs and the web UI. Build context: the repository root.
ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df

# Swagger UI for /docs, baked into the image (no CDN at runtime), checked against its sha256.
FROM ${PYTHON_IMAGE} AS swagger
ARG SWAGGER_UI_VERSION=5.34.1
ARG SWAGGER_UI_SHA256=8e7286de0230e431fc8a5a75490d28117af3d0c306413b6f9e17f7bb55045fb6
RUN python -c "import hashlib, io, sys, tarfile, urllib.request; \
url = 'https://registry.npmjs.org/swagger-ui-dist/-/swagger-ui-dist-${SWAGGER_UI_VERSION}.tgz'; \
data = urllib.request.urlopen(url, timeout=60).read(); \
got = hashlib.sha256(data).hexdigest(); \
sys.exit('swagger-ui-dist checksum mismatch: ' + got) if got != '${SWAGGER_UI_SHA256}' else None; \
t = tarfile.open(fileobj=io.BytesIO(data)); \
[t.extract(m, '/tmp', filter='data') for m in t.getmembers() \
 if m.name in ('package/swagger-ui-bundle.js', 'package/swagger-ui.css', 'package/LICENSE')]" \
 && mkdir -p /swagger && mv /tmp/package/* /swagger/

FROM ${PYTHON_IMAGE}
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /srv
COPY requirements/runtime.txt /tmp/runtime.txt
RUN pip install --root-user-action=ignore -r /tmp/runtime.txt
COPY gateway/localai_gateway ./localai_gateway
COPY gateway/static ./static
COPY --from=swagger /swagger ./static/vendor/swagger
RUN useradd --uid 10001 --create-home gateway && mkdir -p /data && chown gateway /data
USER gateway
ENV STATIC_DIR=/srv/static DATA_DIR=/data
EXPOSE 8090
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/healthz', timeout=2)"
CMD ["uvicorn", "localai_gateway.main:app", "--host", "0.0.0.0", "--port", "8090", "--no-access-log"]
