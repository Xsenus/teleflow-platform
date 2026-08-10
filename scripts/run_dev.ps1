$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force -Path "data", "storage/media" | Out-Null
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8080
