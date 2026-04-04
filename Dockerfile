FROM python:3.11-slim

# ── System dependencies ──────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    git \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Python dependencies ───────────────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir --upgrade yt-dlp && \
    pip install --no-cache-dir bgutil-ytdlp-pot-provider

# ── bgutil POT server setup (YouTube bot bypass) ─────────────────────────────
RUN git clone --single-branch --branch 1.3.1 \
    https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git \
    /bgutil && \
    cd /bgutil/server && \
    npm ci && \
    npx tsc

# ── Application code ──────────────────────────────────────────────────────────
COPY . .

RUN mkdir -p /app/cookies && chmod 777 /app/cookies
RUN mkdir -p /app/temp   && chmod 777 /app/temp

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1

# bgutil server background শুরু করো, তারপর Flask চালাও
CMD node /bgutil/server/build/main.js --port 4416 & \
    sleep 5 && \
    gunicorn app:app \
      --bind 0.0.0.0:8000 \
      --workers 2 \
      --timeout 120 \
      --log-level info
