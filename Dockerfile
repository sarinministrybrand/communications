# syntax=docker/dockerfile:1.7
# Multi-stage so the runtime image carries no build toolchain.
FROM python:3.12-slim AS deps
WORKDIR /app
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim AS runtime
# Non-root. A container that can write its own code is a container an RCE owns.
RUN useradd --create-home --uid 10001 comms
WORKDIR /app
COPY --from=deps /install /usr/local
COPY app ./app
COPY web ./web
COPY db ./db
USER comms

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
EXPOSE 8000

# readyz, not healthz: healthz is liveness and always answers, so using it here
# would mark a pod ready before Postgres is reachable.
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/readyz',timeout=2).status==200 else 1)"

# One worker per container; scale with replicas, not with --workers, so the
# orchestrator sees real per-process health.
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
