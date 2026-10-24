FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng poppler-utils \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home agents && mkdir /data && chown agents:agents /data
WORKDIR /app
COPY requirements.lock pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.lock
COPY extraction_service ./extraction_service
RUN pip install --no-cache-dir --no-deps .
USER 10001:10001
CMD ["uvicorn", "extraction_service.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--limit-concurrency", "16"]
