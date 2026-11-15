# YTDL-UI — multi-arch image (linux/amd64, linux/arm64)
#   docker build -t ytdl-ui .
#   docker run -p 5000:5000 -v ytdlui-data:/app/data ytdl-ui

# Deno is the JavaScript runtime yt-dlp uses to unlock YouTube's best formats.
FROM denoland/deno:bin-2.9.8 AS deno

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    YTDLUI_DOCKER=1 \
    YTDLUI_HOST=0.0.0.0 \
    YTDLUI_PORT=5000 \
    YTDLUI_FFMPEG_AUTO_INSTALL=false

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates tini \
 && rm -rf /var/lib/apt/lists/*

COPY --from=deno /deno /usr/local/bin/deno

RUN useradd --system --create-home --home-dir /home/ytdlui --shell /usr/sbin/nologin ytdlui \
 && python -m venv /opt/venv \
 && mkdir -p /app/data \
 && chown -R ytdlui:ytdlui /opt/venv /app

WORKDIR /app
COPY --chown=ytdlui:ytdlui requirements.txt .
USER ytdlui
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=ytdlui:ytdlui server.py config.example.ini ./
COPY --chown=ytdlui:ytdlui ytdlui ./ytdlui
COPY --chown=ytdlui:ytdlui static ./static

VOLUME ["/app/data"]
EXPOSE 5000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('YTDLUI_PORT','5000'), timeout=4)"

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "server.py"]
