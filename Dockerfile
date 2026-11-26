# The image is built to fit Render's free tier: 512 MB of RAM, 0.5 vCPU.
#
#   docker build -t tiktok-engine .
#   docker build --build-arg SYSTEM_CHROME=1 -t tiktok-engine .
#
# SYSTEM_CHROME=1 uses Debian's chromium instead of Playwright's own build.
# It skips a ~420 MB download and keeps the image far smaller; set
# BROWSER_EXECUTABLE_PATH=/usr/bin/chromium to match.
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
        libcups2 \
        libdrm2 \
        libxkbcommon0 \
        libxcomposite1 \
        libxdamage1 \
        libxfixes3 \
        libxrandr2 \
        libgbm1 \
        libpango-1.0-0 \
        libcairo2 \
        libasound2 \
        libx11-6 \
        libxcb1 \
        libxext6 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# The browser. Playwright's own build is the default; SYSTEM_CHROME=1 uses
# Debian's chromium and skips the download entirely.
ARG SYSTEM_CHROME=0
RUN if [ "$SYSTEM_CHROME" = "1" ]; then \
        apt-get update \
        && apt-get install -y --no-install-recommends chromium \
        && rm -rf /var/lib/apt/lists/* \
        && echo "using the system chromium"; \
    else \
        playwright install --with-deps chromium; \
    fi

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
