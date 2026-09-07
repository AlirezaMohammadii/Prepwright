#!/bin/sh
# Regenerate MANIFEST.sha256 over every file the bridge loads at runtime.
#
# Run from the repository root after ANY change to bridge.py, index.html, or
# the prepwright package, then commit the manifest with the change. A stale
# manifest refuses correct code, which is the failure mode that kept the old
# two-file pin empty and therefore enforcing nothing.
#
#   ./tools/make_manifest.sh && git add MANIFEST.sha256
set -eu
cd "$(dirname "$0")/.."
{
  echo "# Prepwright runtime integrity manifest."
  echo "# Every file the bridge loads. Regenerate with tools/make_manifest.sh."
  echo "# Verified by prep-launcher.sh, which refuses to start on a mismatch."
  /usr/bin/shasum -a 256 bridge.py index.html prepwright/*.py
} > MANIFEST.sha256
echo "MANIFEST.sha256: $(grep -c '^[0-9a-f]' MANIFEST.sha256) files pinned"
