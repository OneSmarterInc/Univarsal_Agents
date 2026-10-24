# Universal Agents service image: the API, the workers and the CLI (`agents ...`).
# Build from the repository root:  docker build -f deploy/Dockerfile -t universal-agents .

FROM python:3.12-slim-bookworm AS build
WORKDIR /src
COPY pyproject.toml README.md ./
COPY agents_service ./agents_service
# Every dependency as a wheel, so the final image needs no compiler and no network.
RUN pip wheel --no-cache-dir --wheel-dir /wheels ".[s3]"

FROM python:3.12-slim-bookworm
# Tesseract reads screenshots and scanned pages; tini passes stop signals to the workers.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng tini \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels "universal-agents[s3]" && rm -rf /wheels
# The service runs as its own user, never as root.
RUN useradd --create-home --uid 10001 agents && mkdir -p /app/data && chown agents:agents /app/data
WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    AGENTS_DATA_DIR=/app/data
USER agents
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=4).status == 200 else 1)" || exit 1
ENTRYPOINT ["tini", "--", "agents"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
