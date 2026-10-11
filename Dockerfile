FROM python:3.12-slim

# ffmpeg lets yt-dlp / faster-whisper decode reel audio.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md config.example.toml ./
COPY mosaico ./mosaico
RUN pip install --no-cache-dir ".[transcribe]"

# Config, database, dashboard and generated assets live in the mounted volume.
WORKDIR /srv/mosaico
ENV MOSAICO_CONFIG=/srv/mosaico/config.toml
CMD ["mosaico", "run"]
