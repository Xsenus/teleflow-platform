#!/usr/bin/env sh
set -eu
umask 077

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

if [ "$#" -ne 2 ]; then
  echo "Usage: $0 BACKUP_ARTIFACT BACKUP_RECEIPT_JSON" >&2
  echo "TeleFlow 1.8 restore.sh performs an isolated restore drill and never overwrites production." >&2
  exit 2
fi

set -- python scripts/recovery_restore_drill.py "$1" "$2"
if [ -n "${RECOVERY_ORGANIZATION:-}" ]; then
  set -- "$@" --organization "$RECOVERY_ORGANIZATION"
fi
if [ -n "${RECOVERY_ACTOR_EMAIL:-}" ]; then
  set -- "$@" --actor-email "$RECOVERY_ACTOR_EMAIL"
fi
if [ -n "${AGE_IDENTITY_FILE:-}" ]; then
  set -- "$@" --age-identity-file "$AGE_IDENTITY_FILE"
fi
exec "$@"
