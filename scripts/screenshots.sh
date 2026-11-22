#!/usr/bin/env bash
# screenshots.sh — README screenshots through the real web UI, with a headless Chromium in a container.
# Usage: scripts/screenshots.sh [BASE_URL] [--startup]     (default http://localhost:8090)
# The gateway must be serving the example notes: the pictures go into a public repository.
set -euo pipefail
cd "$(dirname "$0")/.."
BASE=${1:-http://localhost:8090}; shift || true
docker build -q -t local-ai-core/screenshots scripts/screenshots >/dev/null
mkdir -p docs/img
docker run --rm --network host --user "$(id -u):$(id -g)" -e HOME=/tmp \
    -v "$PWD/docs/img:/out" -v "$PWD/services/documents/documents_worker/selftest:/fixtures:ro" \
    -v "$PWD/services/speakers/speakers_worker/selftest:/speech:ro" \
    local-ai-core/screenshots "$BASE" /out "$@"
