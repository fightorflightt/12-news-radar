#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

OWNER="$(python3 - <<'PY'
import json
from pathlib import Path
print(json.loads(Path("config/settings.json").read_text())["github"]["owner"])
PY
)"
REPO="$(python3 - <<'PY'
import json
from pathlib import Path
print(json.loads(Path("config/settings.json").read_text())["github"]["repo"])
PY
)"

if [ ! -d .git ]; then
  git init
  git branch -M main
fi

git add .
if git diff --cached --quiet; then
  echo "No changes to publish."
else
  git commit -m "Update news radar"
fi

if ! gh repo view "$OWNER/$REPO" >/dev/null 2>&1; then
  gh repo create "$OWNER/$REPO" --public --source=. --remote=origin --push
else
  if ! git remote get-url origin >/dev/null 2>&1; then
    git remote add origin "https://github.com/$OWNER/$REPO.git"
  fi
  git push -u origin main
fi

gh api \
  --method POST \
  -H "Accept: application/vnd.github+json" \
  "/repos/$OWNER/$REPO/pages" \
  -f 'source[branch]=main' \
  -f 'source[path]=/docs' >/dev/null 2>&1 || \
gh api \
  --method PUT \
  -H "Accept: application/vnd.github+json" \
  "/repos/$OWNER/$REPO/pages" \
  -f build_type=legacy \
  -f 'source[branch]=main' \
  -f 'source[path]=/docs' >/dev/null 2>&1 || true

echo "Published: https://$OWNER.github.io/$REPO/"
