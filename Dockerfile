# Multi-stage build for single-container deployment
# Stage 1: Build frontend
FROM --platform=$BUILDPLATFORM node:24-alpine AS frontend-builder

WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
# vite.config.ts bakes ../VERSION into the bundle for the update banner
COPY VERSION /VERSION
RUN npm run build

# Stage 2: Final image with backend + frontend
FROM python:3.13-slim-trixie

ARG BRANCH

# Use bash with pipefail for safer pipe handling
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Install system dependencies; pull latest openssl security patches (orig. CVE-2026-28390; trixie renamed libssl3 -> libssl3t64)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    tzdata \
    gosu \
    libcairo2 \
    && apt-get install -y --no-install-recommends --only-upgrade libssl3t64 openssl \
    && rm -rf /var/lib/apt/lists/*

# Copy rclone binary from official image (multi-arch aware)
COPY --from=rclone/rclone:1.74.4 /usr/local/bin/rclone /usr/local/bin/rclone

# Set default timezone (can be overridden by docker-compose)
ENV TZ=UTC
ENV BRANCH=${BRANCH}
# Limit glibc memory arenas to reduce fragmentation and improve malloc_trim effectiveness
ENV MALLOC_ARENA_MAX=2

# Create app user (UID/GID remapped at runtime via entrypoint using PUID/PGID env vars)
RUN groupadd -g 1000 posterflow && \
    useradd -u 1000 -g posterflow -m -s /bin/bash posterflow

# Set working directory
WORKDIR /app

# Copy requirements and install as root
COPY backend/requirements.txt backend/requirements-dev.txt ./
RUN python -m pip install --no-cache-dir "pip==26.1.2" && \
    pip install --no-cache-dir -r requirements.txt && \
    pip uninstall -y pip

# Create necessary directories
RUN mkdir -p /app/frontend/dist

# Copy backend application code
COPY --chown=posterflow:posterflow backend/ .

# Copy VERSION file for version display
COPY --chown=posterflow:posterflow VERSION /VERSION

# Native Photoshop UXP plugin — served as a downloadable .ccx from the PSD settings modal
COPY --chown=posterflow:posterflow photoshop-posterflow/ /app/photoshop-posterflow/

# Copy built frontend from frontend-builder stage
COPY --from=frontend-builder --chown=posterflow:posterflow /frontend/dist /app/frontend/dist

# Copy and configure entrypoint script
COPY --chmod=755 entrypoint.sh /entrypoint.sh

# Expose port (both frontend and backend on same port now)
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1

# Container starts as root; entrypoint remaps PUID/PGID then drops to posterflow user
ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "main.py"]
