#!/usr/bin/env bash
# make-fixtures.sh — rebuilds the test documents of the documents service from examples/documents/invoice.html
# (an invented bill), with the same headless Chromium used for the screenshots.
set -euo pipefail
cd "$(dirname "$0")/.."
docker build -q -t local-ai-core/screenshots scripts/screenshots >/dev/null
out=services/documents/documents_worker/selftest
docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp --entrypoint python3 \
    -v "$PWD/examples/documents:/in:ro" -v "$PWD/$out:/out" local-ai-core/screenshots /make_fixtures.py /in/invoice.html /out
