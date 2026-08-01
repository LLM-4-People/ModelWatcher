# ── Stage 1: Build Tailwind CSS ──────────────────────────────────────────────
FROM node:22-slim AS css-builder
WORKDIR /tmp
COPY package.json ./
COPY frontend/input.css frontend/input.css
COPY frontend/index.html frontend/index.html
# Copy JS modules (excluding vendor/ - Chart.js has no Tailwind class names)
COPY frontend/js/ frontend/js/
RUN npm install && npm run build:css

# ── Stage 2: Python runtime ──────────────────────────────────────────────────
FROM python:3.13-slim-trixie
WORKDIR /app

# Install tzdata for timezone support, create non-root user
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /bin/bash appuser

# Install Node.js from NodeSource (clean, version-pinned, no fragile binary copying)
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

ENV TZ=UTC
ENV NODE_PATH=/opt/node_modules
ENV SYNBAD_BIN=/opt/node_modules/.bin/synbad

# Install Python dependencies (latest stable, no pins - fresh build gets newest)
COPY requirements.txt .
RUN pip install --no-cache-dir -r /app/requirements.txt

# Install Node.js production dependencies (only @syntheticlab/synbad)
WORKDIR /opt
COPY package.json ./
RUN npm install --omit=dev

# Copy application code
WORKDIR /app
COPY backend/ backend/
COPY config/ config/
COPY frontend/ frontend/
COPY --from=css-builder /tmp/frontend/tailwind.min.css /opt/frontend/tailwind.min.css

# Copy example configs (actual config/*.yaml are gitignored - mounted at runtime)
COPY config/*.example config/

# Health check - uses the /health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT:-8080}/health').read()" || exit 1

# Run as non-root user
USER appuser

EXPOSE 8080
CMD ["sh", "-c", "uvicorn backend.main:app --host ${HOST:-0.0.0.0} --port ${PORT:-8080} --reload --reload-dir backend --loop uvloop --proxy-headers --forwarded-allow-ips \"${FORWARDED_ALLOW_IPS:-127.0.0.1}\" --log-level warning --ws-ping-interval 30 --ws-ping-timeout 90"]
