#!/usr/bin/env bash
# make-handwriting-fixtures.sh — rebuilds the test page of the handwriting service (an invented note in the
# Caveat handwriting font, see scripts/screenshots/make_handwriting.py).
set -euo pipefail
cd "$(dirname "$0")/.."
docker build -q -t local-ai-core/screenshots scripts/screenshots >/dev/null
out=services/reader/reader_worker/selftest
mkdir -p "$out"
docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp --entrypoint python3 -v "$PWD/$out:/out" \
    local-ai-core/screenshots /make_handwriting.py /out
