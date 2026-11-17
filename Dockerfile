FROM python:3.12.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates docker.io \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.lock.txt .
RUN pip install --require-hashes -r requirements.lock.txt \
 && pip install setuptools==80.9.0 wheel==0.45.1
COPY . .
RUN pip install --no-build-isolation --no-deps .
ENV PYTHONUNBUFFERED=1
