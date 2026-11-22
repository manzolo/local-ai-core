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

[ $rc = 0 ] && echo "✓ collaudo passed" || echo "✗ collaudo FAILED"
exit $rc
