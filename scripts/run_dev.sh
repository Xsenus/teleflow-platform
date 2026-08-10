#!/usr/bin/env sh
set -eu
mkdir -p data storage/media
exec uvicorn app.main:app --reload --host 0.0.0.0 --port "${PORT:-8080}"
