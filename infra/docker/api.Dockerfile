# Lanternkeeper API + worker image (same image, different command).
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv

# Tesseract enables the OCR fallback (LK_OCR_ENGINE=tesseract).
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir uv==0.13.6

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
COPY packages ./packages
COPY services/api ./services/api
RUN uv sync --frozen --no-dev

RUN useradd --system --uid 10001 --home /app lk \
 && mkdir -p /data/storage && chown -R lk /data
USER lk
ENV PATH="/opt/venv/bin:$PATH" LK_STORAGE_DIR=/data/storage
WORKDIR /app/services/api
EXPOSE 8000
CMD ["uvicorn", "lk_api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
