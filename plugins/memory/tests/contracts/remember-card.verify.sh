#!/usr/bin/env bash
# Verify that a NEW memory card containing "92" was written to $KB_DIR/memory/,
# excluding the two pre-existing fixture cards.
set -euo pipefail

MEMORY_DIR="$KB_DIR/memory"

# Find cards that contain "92" but are NOT the two pre-seeded fixture cards
new_card=$(grep -rl "92" "$MEMORY_DIR" \
  | grep -v "kb-wiki-root" \
  | grep -v "kb-coffee-notes" \
  | head -1)

if [ -z "$new_card" ]; then
  echo "FAIL: no new card containing '92' found in $MEMORY_DIR (excluding fixture cards)"
  exit 1
fi

# New card must have a ^name: frontmatter field
if ! grep -q "^name:" "$new_card"; then
  echo "FAIL: new card $new_card missing '^name:' frontmatter"
  exit 1
fi

echo "PASS: new card at $new_card contains '92' and has 'name:' field"
