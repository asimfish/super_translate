#!/bin/bash
# Daily backup of Super Translate state (DB + uploaded papers + terminology + env).
# Translated PDFs are regenerable and excluded to keep backups small.
set -euo pipefail
SRC="$HOME/super_translate"
DEST="$HOME/backups/super_translate/$(date +%F)"
mkdir -p "$DEST"
# SQLite: consistent online backup, not a file copy (WAL mode).
sqlite3 "$SRC/data/paper_china.db" ".backup $DEST/paper_china.db"
rsync -a --delete "$SRC/data/papers/" "$DEST/papers/"
[ -f "$SRC/data/terminology_candidates.jsonl" ] && cp "$SRC/data/terminology_candidates.jsonl" "$DEST/"
[ -f "$SRC/.env" ] && cp "$SRC/.env" "$DEST/env.backup"
# Keep the newest 14 daily snapshots.
ls -1d "$HOME/backups/super_translate"/2* 2>/dev/null | sort -r | tail -n +15 | xargs -r rm -rf
