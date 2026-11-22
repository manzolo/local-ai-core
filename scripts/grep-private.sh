#!/usr/bin/env bash
# grep-private.sh — run before every commit and before publishing: nothing private may reach the repo.
#
# Generic checks live here: home paths, LAN addresses and hostnames, e-mail addresses, keys.
# Names that are private by themselves (people, machines, projects, employers) must not be written in
# this public file: put them one regular expression per line in .private-patterns, which git ignores.
# Images are not searched: look at every screenshot before committing it.
# --history also searches every commit (run it before publishing: history is published too).
# Exit: 0 clean · 1 something found.
set -uo pipefail
cd "$(dirname "$0")/.."
generic=(
    '/home/[a-z]'                                   # home directories
    '\b192\.168\.[0-9]+\.[0-9]+'                    # LAN addresses
    '(^|[^=.0-9`])10\.[0-9]+\.[0-9]+\.[0-9]+\b'  # not a version like ==10.4.0.35 or `10.4.0.35`
    '\b[a-z0-9-]+\.lan\b'                           # LAN hostnames
    '\b[A-Za-z0-9._%-]+@[A-Za-z0-9-]+\.[A-Za-z]{2,}\b' # e-mail addresses
    '(api[_-]?key|token|password|secret)\s*[:=]\s*["'"'"']?[A-Za-z0-9/+_-]{12,}'
)
pats=("${generic[@]}")
if [ -f .private-patterns ]; then
    while IFS= read -r p; do [ -n "$p" ] && [ "${p:0:1}" != "#" ] && pats+=("$p"); done < .private-patterns
else
    echo "· no .private-patterns file: only the generic checks run"
fi
files=$(git ls-files --cached --others --exclude-standard | grep -vE '\.(png|jpe?g|gif|webp|ico)$' | grep -v '^scripts/grep-private.sh$')
found=0
for p in "${pats[@]}"; do
    hits=$(printf '%s\n' "$files" | xargs -r -d '\n' grep -nIiE -- "$p" 2>/dev/null)
    if [ -n "$hits" ]; then echo "✗ /$p/"; printf '%s\n' "$hits" | sed 's/^/    /'; found=1; fi
done
if [ "${1:-}" = "--history" ]; then
    for p in "${pats[@]}"; do
        hits=$(git log --all -p --format='commit %h' -- . ':!scripts/grep-private.sh' 2>/dev/null | grep -iE -- "$p" | head -5)
        if [ -n "$hits" ]; then echo "✗ in history: /$p/"; printf '%s\n' "$hits" | sed 's/^/    /'; found=1; fi
    done
fi
if [ "${1:-}" = "--history" ]; then
    echo "· commit authors (published with every commit):"
    git log --all --format='    %an <%ae>' | sort -u
fi
imgs=$(printf '%s\n' "$(git ls-files --cached --others --exclude-standard)" | grep -cE '\.(png|jpe?g|gif|webp)$')
[ "$imgs" -gt 0 ] && echo "· $imgs images not searched: check them by eye"
[ $found = 0 ] && echo "✓ nothing private found in $(printf '%s\n' "$files" | wc -l) files"
exit $found
