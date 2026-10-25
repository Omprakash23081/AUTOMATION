FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    XDG_CACHE_HOME=/tmp/.cache \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt
RUN python -m playwright install --with-deps chromium

COPY server.py automation_integration.py index.html api-config.js gunicorn.conf.py chatgpt_browser_automation.py local_config.py ./
COPY *.js *.css ./
COPY YOUTUBE_VIDEO_AUTOMATION/youtube_automation ./YOUTUBE_VIDEO_AUTOMATION/youtube_automation
RUN mkdir -p /app/data/tmp && chown -R 10001:10001 /app
USER 10001:10001

EXPOSE 5000
CMD ["python", "server.py"]
