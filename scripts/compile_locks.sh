#!/usr/bin/env bash
set -euo pipefail

output_dir="${1:-requirements}"
mkdir -p "$output_dir"
for group in bot userbot tools dev; do
  uv pip compile \
    --python-version 3.13 \
    --generate-hashes \
    --no-emit-index-url \
    --output-file "$output_dir/$group.lock" \
    "requirements/$group.in"
done
