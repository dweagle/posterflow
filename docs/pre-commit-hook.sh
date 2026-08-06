#!/usr/bin/env bash
# Blocks a commit that changes the drive page's sources without a matching rebuild.
# Reinstall: cp docs/pre-commit-hook.sh .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit
# Skip once: git commit --no-verify

set -uo pipefail

root=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
cd "$root" || exit 0

PAGE="docs/gdrives.html"
BUILDER="docs/build_gdrives.py"
SOURCES='^(backend/assets/(drives|artwork_drives)\.json|docs/(gdrives_notes\.json|build_gdrives\.py|gdrives\.html))$'

git diff --cached --name-only --diff-filter=ACMR | grep -qE "$SOURCES" || exit 0

[ -f "$BUILDER" ] || exit 0
if ! command -v python3 >/dev/null 2>&1; then
    echo "[gdrives] python3 not found - skipping the generated-page check." >&2
    exit 0
fi

if ! output=$(python3 "$BUILDER" --check 2>&1); then
    echo >&2
    echo "$output" >&2
    cat >&2 <<'MSG'

[gdrives] Commit aborted: docs/gdrives.html is out of sync with its sources, or a
          drive still needs a gdrives_notes.json entry (see above).

    python3 docs/build_gdrives.py
    git add docs/gdrives.html

Then commit again, or skip this check with: git commit --no-verify
MSG
    exit 1
fi

# current on disk but not staged = the commit would still carry stale tables
if ! git diff --quiet -- "$PAGE"; then
    cat >&2 <<'MSG'

[gdrives] Commit aborted: docs/gdrives.html has unstaged changes, so this commit
          would carry stale tables.

    git add docs/gdrives.html

Then commit again, or skip this check with: git commit --no-verify
MSG
    exit 1
fi

exit 0
