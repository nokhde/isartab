FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY requirements.txt .env* ./

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1

# IMPORTANT: run with exactly ONE worker process (uvicorn defaults to 1 — do
# NOT add `--workers N>1`, and do NOT front this with gunicorn running multiple
# workers). The whole app shares a single in-memory SQLite connection held in
# module scope (app/db.py). Each extra worker process would get its OWN empty
# database: registrations would silently split across processes with no error,
# corrupting the event. The single connection is serialised with a semaphore,
# so one process handles concurrent requests safely. Scale vertically, not by
# adding workers.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
