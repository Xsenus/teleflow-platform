FROM python:3.14-slim AS runtime

LABEL org.opencontainers.image.title="TeleFlow Platform" \
      org.opencontainers.image.version="2.5.0" \
      org.opencontainers.image.description="Secure Telegram publishing and Business automation platform"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl tini age postgresql-client \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system teleflow \
    && useradd --system --gid teleflow --create-home --home-dir /home/teleflow teleflow

WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install --requirement requirements.txt

COPY --chown=teleflow:teleflow . .
RUN mkdir -p /app/data /app/storage/media /app/storage/exports /app/storage/backups \
    && chown -R teleflow:teleflow /app

USER teleflow
EXPOSE 8080
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl --fail http://127.0.0.1:8080/api/v1/health/live || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"]
