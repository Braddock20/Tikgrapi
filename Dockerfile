# The image is built to fit Render's free tier: 512 MB of RAM, 0.5 vCPU.
#
#   docker build -t tiktok-engine .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PORT=10000

WORKDIR /app

# The libraries Chromium needs, and the fonts the page renderer needs.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        fonts-liberation \
        fonts-noto-color-emoji \
        libatomic1 \
        libnss3 \
        libnspr4 \
        libatk1.0-0 \
        libatk-bridge2.0-0 \
        libatspi2.0-0 \
        libcups2 \
        libdbus-1-3 \
        libdrm2 \
        libexpat1 \
        libfontconfig1 \
        libfreetype6 \
        libglib2.0-0 \
        libx11-6 \
        libx11-xcb1 \
        libxcb1 \
        libxcomposite1 \
        libxdamage1 \
        libxext6 \
        libxfixes3 \
        libxkbcommon0 \
        libxrandr2 \
        libxshmfence1 \
        libgbm1 \
        libpango-1.0-0 \
        libcairo2 \
        libasound2 \
        libwayland-client0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Install the exact Chromium revision required by the pinned Playwright.
# Do NOT use `--with-deps`: Debian Trixie removed legacy ttf-* package names
# requested by Playwright 1.49's dependency installer. Runtime libraries are
# installed explicitly above.
RUN playwright install chromium

COPY . .

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data/media /data/queue \
    && chown -R appuser:appuser /app /data
USER appuser

ENV SESSION_PATH=/data/session.enc \
    MEDIA_PATH=/data/media \
    DATABASE_URL=sqlite+aiosqlite:////data/engine.db

EXPOSE 10000
HEALTHCHECK --interval=45s --timeout=6s --start-period=25s --retries=3 \
    CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','10000')+'/health',timeout=5).read()"

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000} --no-access-log --timeout-keep-alive 65"]
