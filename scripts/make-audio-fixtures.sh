#!/usr/bin/env bash
# make-audio-fixtures.sh — rebuilds the test speech of the transcriber and speakers services with Piper and
# two public-domain voices (see scripts/audio-fixtures/make_audio.py).
set -euo pipefail
cd "$(dirname "$0")/.."
docker build -q -t local-ai-core/audio-fixtures scripts/audio-fixtures >/dev/null
tr=services/transcriber/transcriber_worker/selftest; sp=services/speakers/speakers_worker/selftest
mkdir -p "$tr" "$sp"
docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -v "$PWD/$tr:/tr" -v "$PWD/$sp:/sp" local-ai-core/audio-fixtures /tr /sp
