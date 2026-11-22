#!/usr/bin/env bash
# mutations.sh — proves the tests catch real mistakes: each line breaks the code on purpose, runs the
# test suite (which must fail), then restores the file. A mutation that survives is a hole in the tests.
# Usage: scripts/mutations.sh [PYTEST]   (default: pytest from PATH)
set -uo pipefail
cd "$(dirname "$0")/.."
PYTEST=${1:-pytest}
rc=0
mut() {  # mut FILE OLD NEW DESCRIPTION
    cp "$1" "$1.orig"
    if ! python3 -c 'import sys; p,o,n=sys.argv[1:4]; s=open(p).read(); assert o in s; open(p,"w").write(s.replace(o,n,1))' "$1" "$2" "$3"; then
        mv "$1.orig" "$1"; echo "? $4: the code changed, update this mutation"; rc=1; return
    fi
    out=$($PYTEST -q -p no:warnings 2>&1 | tail -1)
    mv "$1.orig" "$1"
    case "$out" in *failed*) echo "✓ caught: $4 ($out)" ;; *) echo "✗ NOT caught: $4 ($out)"; rc=1 ;; esac
}
trap 'for f in $(find gateway services -name "*.orig"); do mv "$f" "${f%.orig}"; done' EXIT

mut services/search/search_worker/ask.py 'target = seen if 1 <= k <= n_sources else bad' 'target = seen' \
    "an invented citation is accepted"
mut services/search/search_worker/index.py 'or len(vec) != dim * len(meta.get("chunks", []))' '' \
    "a truncated index is accepted"
mut services/search/search_worker/index.py 'if old_meta["files"].get(rel) == sha and rel in by_file:' 'if False:' \
    "every update re-reads every file"
mut services/search/search_worker/main.py $'    if not sources:\n        yield' $'    if False:\n        yield' \
    "the model is called with no excerpts"
mut gateway/localai_gateway/main.py 'if not got or not hmac.compare_digest(got.encode(), key.encode()):' 'if not got:' \
    "a wrong API key is accepted"
mut gateway/localai_gateway/main.py 'if state["received"] > self.limit:' 'if False:' \
    "the upload limit is ignored for chunked bodies"
mut gateway/localai_gateway/jobs.py '                async with self.gpu:' '                async with asyncio.Lock():' \
    "the GPU queue lets every job through"
mut gateway/localai_gateway/clients.py 'layers[d["digest"]] = (d.get("completed") or 0, d["total"])' \
    'layers = {d["digest"]: (d.get("completed") or 0, d["total"])}' \
    "the download progress shows one layer instead of the whole model"
mut services/documents/documents_worker/main.py '    if not md.strip():' '    if False:' \
    "a scan read without OCR counts as converted"
mut services/documents/documents_worker/main.py '    if not (inside_uploads(req.path) and inside_uploads(req.out)):' '    if False:' \
    "the documents worker reads files outside the uploads volume"
mut gateway/localai_gateway/main.py '                shutil.rmtree(job_dir, ignore_errors=True)' '                pass' \
    "uploaded files outlive their job"
mut gateway/localai_gateway/main.py '    if free >= needed_gb:' '    if True:' \
    "a full GPU does not make Ollama free its models"
mut gateway/localai_gateway/subtitles.py "return f\"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}\"" "return f\"{h:02d}:{m:02d}:{s:02d}.{ms:03d}\"" \
    "SRT timestamps use a dot instead of a comma"
mut services/speakers/speakers_worker/main.py '        order.setdefault(label, len(order) + 1)' '        order.setdefault(label, label + 1)' \
    "speakers are not numbered in order of first speech"
mut services/speakers/speakers_worker/main.py '    if len(X) == 1 or n == 1:' '    if True:' \
    "every voice is put into one speaker"
mut services/transcriber/transcriber_worker/main.py '        result["matches"] = result["score"] >= MATCH' '        result["matches"] = True' \
    "a transcript that says something else still matches"
mut gateway/localai_gateway/main.py '        sw = await worker_for("speakers") if speakers else None' '        sw = None' \
    "speakers=true is silently ignored"
mut services/image/image_worker/workflows.py 'def cutout(image, model, prefix="lac-cutout", invert=True):' 'def cutout(image, model, prefix="lac-cutout", invert=False):' \
    "the cut-out mask is not inverted"
mut services/image/image_worker/workflows.py '    if scale == 2:' '    if False:' \
    "upscale x2 stays x4"
mut services/image/image_worker/models.py '            if h.hexdigest() != m.sha256:' '            if False:' \
    "a downloaded model is used without checking its sha256"
mut gateway/localai_gateway/jobs.py '            if self.on_evict:' '            if False:' \
    "result images outlive their job"
mut gateway/localai_gateway/main.py '    for w in app.state.workers.values():
        if w.name == exclude:' '    for w in []:
        if w.name == exclude:' \
    "a full GPU does not make the other workers free their VRAM"
mut gateway/localai_gateway/clients.py '            if attempt == RESTART_WAIT - 1:' '            if True:' \
    "a worker restarting to free VRAM is reported as down"
mut services/transcriber/transcriber_worker/main.py '    if unloaded:
        restart_soon()' '    if False:
        restart_soon()' \
    "unloading keeps the CUDA context (no restart)"
exit $rc
