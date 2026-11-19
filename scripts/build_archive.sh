#!/bin/sh
set -e
root=$(dirname "$0")/..
cd "$root"
name="transfer-core-$(git rev-parse --short HEAD 2>/dev/null || echo unversioned).zip"
git archive -o "$name" HEAD
if unzip -l "$name" | tail -n +4 | head -n -2 | grep -Eq '(^|/)\.env$|\.zip$|__pycache__|\.pytest_cache|\.ruff_cache|\.venv/'; then
  echo "archive contains artifacts or secrets" >&2
  exit 1
fi
echo "$name"
