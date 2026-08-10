#!/usr/bin/env sh
set -eu
umask 077

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

# Compatibility wrapper for operators who used scripts/backup.sh before 1.8.
# The Python implementation creates a signed receipt and signed internal
# manifest. It does not print or persist decrypted secrets.
set -- python scripts/recovery_backup.py
if [ -n "${BACKUP_ROOT:-}" ]; then
  set -- "$@" --output-dir "$BACKUP_ROOT/recovery"
fi
if [ -n "${BACKUP_AGE_RECIPIENT:-}" ]; then
  set -- "$@" --age-recipient "$BACKUP_AGE_RECIPIENT"
fi
if [ "${WITHOUT_STORAGE:-false}" = "true" ]; then
  set -- "$@" --without-storage
fi
if [ -n "${RECOVERY_ORGANIZATION:-}" ]; then
  set -- "$@" --organization "$RECOVERY_ORGANIZATION"
fi
if [ -n "${RECOVERY_ACTOR_EMAIL:-}" ]; then
  set -- "$@" --actor-email "$RECOVERY_ACTOR_EMAIL"
fi
exec "$@"
