FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# Chromium + the fonts the page renderer needs. The browser backend drives a
# real Chromium so requests carry the signature the site produces.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    fonts-liberation \
    fonts-noto-color-emoji \
    libatomic1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps chromium

# The vendored TikTok client lives in ./vendor and is not on PyPI — install it
# from the tree so `import tiktokrapi` works (the upload pipeline needs it).
COPY vendor ./vendor
RUN pip install --no-cache-dir ./vendor

COPY . .

# FFmpeg renders the videos; the fonts below are the ones the video engine
# pairs with (the engine ships its own set under assets/fonts, these are the
# system fallbacks).
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home appuser \
    && mkdir -p /data/media \
    && chown -R appuser:appuser /app /data
USER appuser

ENV SESSION_PATH=/data/session.enc \
    MEDIA_PATH=/data/media

EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000}"]
