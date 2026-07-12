# insider-intel FastAPI (search / reader / extract) for Cloud Run
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PROCESSED_ARTICLES_PATH=/app/data/processed/articles.jsonl \
    RAW_ARTICLES_PATH=/app/data/raw/articles.jsonl \
    CORS_ORIGINS=https://intel.thederpweb.com,https://scubber.github.io,http://127.0.0.1:5500,http://localhost:5500 \
    PORT=8080

WORKDIR /app

# Install package first (better layer cache), then bake corpus
COPY pyproject.toml README.md ./
COPY apps ./apps
COPY shared ./shared
RUN pip install --upgrade pip && pip install .

# Baked corpus — rebuild image after ingest/process to refresh public API
RUN mkdir -p /app/data/processed /app/data/raw
COPY data/processed/articles.jsonl /app/data/processed/articles.jsonl

EXPOSE 8080

# Cloud Run sets PORT; default 8080 for local docker run
CMD ["sh", "-c", "uvicorn apps.search.api:app --host 0.0.0.0 --port ${PORT:-8080}"]
