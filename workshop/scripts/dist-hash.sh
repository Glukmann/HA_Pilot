#!/usr/bin/env bash
# Print the content hash of workshop/dist for workshop/dist.sha256.
#
# Aggregates per-file sha256 over the sorted file list, so Vite-hashed asset
# names don't matter: same sources produce the same hash on any machine.
# Regenerate after rebuilding the bundle: npm run dist:hash
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -d dist ]; then
  echo "workshop/dist is missing — run: npm ci && npm run build" >&2
  exit 1
fi

find dist -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum | cut -d' ' -f1
