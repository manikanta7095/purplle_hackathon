# Production image for the Store Intelligence API (FastAPI + uvicorn).
# The detection pipeline runs offline on the host; this container serves analytics only.
#
# Local:  docker compose up --build
# Render: Web Service, Docker, set STORE_INTEL_DB=/app/data/store_intelligence.db
#         (use a persistent disk or Postgres for production data)

FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PORT=8000 \
    STORE_INTEL_DB=/app/data/store_intelligence.db

COPY requirements-api.txt .
RUN pip install --no-cache-dir -r requirements-api.txt

COPY app/ ./app/
COPY scripts/ ./scripts/

RUN mkdir -p /app/data /app/output

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')" || exit 1

# Render and other PaaS set PORT; default 8000 for local docker compose
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
