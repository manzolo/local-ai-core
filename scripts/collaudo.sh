#!/usr/bin/env bash
# collaudo.sh — the real test, on the real GPU, against a running stack (make collaudo).
#
#   1. waits until the gateway is ready (models downloaded, notes indexed)
#   2. every running service must be ready in /v1/services
#   3. runs each service's built-in test: known data → expected output, plus introduced errors
#   4. with the example notes, asks a question end to end and checks the answer and its source
#
# Usage: scripts/collaudo.sh [BASE_URL]      (default http://localhost:8090; LOCAL_AI_API_KEY is sent if set)
# Exit: 0 all passed · 1 something failed.
set -uo pipefail
BASE=${1:-http://localhost:8090}
WAIT=${COLLAUDO_WAIT:-1800}
H=(-H "Content-Type: application/json")
[ -n "${LOCAL_AI_API_KEY:-}" ] && H+=(-H "X-API-Key: $LOCAL_AI_API_KEY")
rc=0
ok()  { echo "  ✓ $*"; }
ko()  { echo "  ✗ $*"; rc=1; }
get() { curl -sf "${H[@]}" "$BASE$1"; }
post() { curl -sf -X POST "${H[@]}" -d "${2:-{\}}" "$BASE$1"; }
py()  { python3 -I -c "$1"; }

echo "▸ gateway $BASE"
t0=$(date +%s)
while :; do
    phase=$(get /v1/status | py 'import json,sys; s=json.load(sys.stdin)["startup"]; print(s["phase"], s.get("detail") or "", s.get("error") or "")' 2>/dev/null)
    case "$phase" in ready*) ok "ready after $(( $(date +%s) - t0 )) s"; break ;; esac
    if [ $(( $(date +%s) - t0 )) -gt "$WAIT" ]; then ko "not ready after $WAIT s: ${phase:-no answer}"; exit 1; fi
    sleep 5
done

echo "▸ services"
services=$(get /v1/services) || { ko "/v1/services failed"; exit 1; }
py 'import json,sys
for s in json.load(sys.stdin)["services"]:
    print(s["name"], s["state"], s.get("worker", ""))' <<<"$services" | while read -r name state worker; do
    case "$state" in ready) ok "$name ready" ;; off) echo "  · $name off (profile not enabled)" ;; *) ko "$name: $state" ;; esac
done
py 'import json,sys; sys.exit(any(s["state"] not in ("ready","off") for s in json.load(sys.stdin)["services"]))' <<<"$services" || rc=1

echo "▸ built-in tests"
for worker in $(py 'import json,sys; print(" ".join(sorted({s["worker"] for s in json.load(sys.stdin)["services"] if s["state"] == "ready"})))' <<<"$services"); do
    res=$(post "/v1/services/$worker/test?wait=true") || { ko "$worker: the test did not run"; continue; }
    py 'import json,sys
r = json.load(sys.stdin)["result"]
for c in r["checks"]:
    print(("  ✓ " if c["ok"] else "  ✗ ") + c["name"] + " — " + c["detail"][:110].replace("\n", " "))
sys.exit(0 if r["ok"] else 1)' <<<"$res" || rc=1
done

echo "▸ end to end"
if get "/v1/search?q=bathroom+tap+invoice&n=1" | grep -q '"house/repairs.md"'; then
    res=$(post "/v1/ask?wait=true" '{"question":"How much did the bathroom repair cost?"}')
    if py 'import json,sys
r = json.load(sys.stdin)["result"]
print("   ", r["answer"][:150])
files = [c["file"] for c in r["cited"]]
sys.exit(0 if "180" in r["answer"] and "house/repairs.md" in files and not r["invalid"] else 1)' <<<"$res"; then
        ok "answer from the example notes, with its source"
    else ko "end-to-end answer wrong: $res"; fi
else
    echo "  · NOTES_DIR is not the example notes: end-to-end question skipped"
fi

if py 'import json,sys; sys.exit(not any(s["name"] == "documents" and s["state"] == "ready" for s in json.load(sys.stdin)["services"]))' <<<"$services"; then
    echo "▸ documents → library → ask"
    here=$(cd "$(dirname "$0")/.." && pwd)
    pdf="$here/services/documents/documents_worker/selftest/invoice-scan.pdf"
    curl -sf -X POST ${LOCAL_AI_API_KEY:+-H "X-API-Key: $LOCAL_AI_API_KEY"} -F "file=@$pdf;filename=collaudo-invoice.pdf" \
        -F add_to_library=true "$BASE/v1/documents/convert?wait=true" >/dev/null \
        && ok "scan converted into library/collaudo-invoice.md" || ko "conversion failed"
    res=$(post "/v1/ask?wait=true" '{"question":"What is the total of the electricity bill?"}')
    if py 'import json,sys
r = json.load(sys.stdin)["result"]
print("   ", r["answer"][:150])
sys.exit(0 if "71.25" in r["answer"] and any(c["file"] == "library/collaudo-invoice.md" for c in r["cited"]) else 1)' <<<"$res"; then
        ok "answer from the converted scan, with its source"
    else ko "answer about the converted document wrong: $res"; fi
    curl -sf -X DELETE ${LOCAL_AI_API_KEY:+-H "X-API-Key: $LOCAL_AI_API_KEY"} "$BASE/v1/library/collaudo-invoice.md?wait=true" >/dev/null \
        && ok "removed from the library and the index" || ko "could not remove the test document"
fi

if py 'import json,sys; s={x["name"]: x["state"] for x in json.load(sys.stdin)["services"]}; sys.exit(not (s.get("transcribe") == "ready" and s.get("speakers") == "ready"))' <<<"$services"; then
    echo "▸ transcribe + speakers"
    here=$(cd "$(dirname "$0")/.." && pwd)
    res=$(curl -sf -X POST ${LOCAL_AI_API_KEY:+-H "X-API-Key: $LOCAL_AI_API_KEY"} -F "file=@$here/services/speakers/speakers_worker/selftest/dialogue.ogg" \
          -F speakers=true "$BASE/v1/transcribe?wait=true")
    if py 'import json,sys
r = json.load(sys.stdin)["result"]
seq = "".join(str(s["speaker"]) for s in r["segments"])
print("   ", r["speakers"], "speakers,", seq, "·", r["segments"][1]["text"][:70])
sys.exit(0 if r["speakers"] == 2 and seq.startswith("12") and "window" in r["text"] and r["srt"].startswith("1\n00:00:00") else 1)' <<<"$res"; then
        ok "dialogue transcribed, two speakers, subtitles"
    else ko "transcription with speakers wrong: ${res:0:300}"; fi
fi

if py 'import json,sys; s={x["name"]: x["state"] for x in json.load(sys.stdin)["services"]}; sys.exit(s.get("images") != "ready")' <<<"$services"; then
    echo "▸ images, then ask: the GPU is shared"
    res=$(post "/v1/images/generate?wait=true" '{"prompt":"a lighthouse on a cliff at sunset, oil painting","width":1024,"height":1024,"seed":7}')
    if py 'import json,sys; r=json.load(sys.stdin)["result"]; print("   ", r["width"], "x", r["height"], "in", r["secs"], "s"); sys.exit(0 if (r["width"], r["height"]) == (1024, 1024) else 1)' <<<"$res"; then
        ok "1024×1024 picture generated"
    else ko "generation failed: ${res:0:300}"; fi
    res=$(post "/v1/ask?wait=true" '{"question":"When is the stove serviced?"}')
    if py 'import json,sys; r=json.load(sys.stdin)["result"]; print("   ", r["answer"][:110]); sys.exit(0 if r["grounded"] else 1)' <<<"$res"; then
        ok "ask right after an image: answered, the GPU made room"
    else ko "ask after an image failed: ${res:0:300}"; fi
    get /v1/status | py 'import json,sys; s=json.load(sys.stdin); g=s["gpu"] or {}; print("    VRAM now", g.get("vram_used_gb"), "of", g.get("vram_total_gb"), "GB; in Ollama:", ", ".join(m["name"] for m in s["ollama"]["loaded"]) or "nothing")'
fi

here=$(cd "$(dirname "$0")/.." && pwd)
if py 'import json,sys; s={x["name"]: x["state"] for x in json.load(sys.stdin)["services"]}; sys.exit(s.get("handwriting") != "ready")' <<<"$services"; then
    echo "▸ handwriting"
    note="$here/services/reader/reader_worker/selftest/note"
    res=$(curl -sf -X POST ${LOCAL_AI_API_KEY:+-H "X-API-Key: $LOCAL_AI_API_KEY"} -F "file=@$note.jpg" \
          --form-string "expect=$(cat "$note.txt")" "$BASE/v1/handwriting?wait=true")
    if py 'import json,sys; r=json.load(sys.stdin)["result"]; print("   ", " ".join(r["text"].split())[:100]); sys.exit(0 if r["matches"] else 1)' <<<"$res"; then
        ok "handwritten note read, similarity ≥ 0.8"
    else ko "handwriting wrong: ${res:0:300}"; fi
fi
if py 'import json,sys; s={x["name"]: x["state"] for x in json.load(sys.stdin)["services"]}; sys.exit(s.get("voice") != "ready")' <<<"$services"; then
    echo "▸ voice (non-commercial license accepted in .env)"
    res=$(post "/v1/speak?wait=true" '{"text":"Welcome home. The heating has been on since 6:30, and dinner is at 8.","format":"ogg"}')
    if py 'import json,sys; r=json.load(sys.stdin)["result"]; v=r["verify"]; print("   ", r["duration"], "s,", r["pieces"], "pieces, lowest similarity", v["min_score"], ",", v["retried"], "redone"); sys.exit(0 if v["checked"] and not v["below"] else 1)' <<<"$res"; then
        ok "spoken, every piece listened to again and right"
    else ko "voice wrong: ${res:0:300}"; fi
fi

[ $rc = 0 ] && echo "✓ collaudo passed" || echo "✗ collaudo FAILED"
exit $rc
