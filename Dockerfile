# Multi-stage build for single-container deployment
# Stage 1: Build frontend
FROM --platform=$BUILDPLATFORM node:22-alpine AS frontend-builder

WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: Final image with backend + frontend
FROM python:3.12-slim-bookworm

ARG BUILD_NUMBER
ARG BRANCH

# Use bash with pipefail for safer pipe handling
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Install system dependencies including rclone, gosu, and timezone data
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
    gcc \
    ca-certificates \
    curl \
    unzip \
    tzdata \
    gosu \
    && curl https://rclone.org/install.sh | bash \
    && rm -rf /var/lib/apt/lists/*

# Set default timezone (can be overridden by docker-compose)
ENV TZ=UTC
ENV BUILD_NUMBER=${BUILD_NUMBER}
ENV BRANCH=${BRANCH}

# Create app user (UID/GID remapped at runtime via entrypoint using PUID/PGID env vars)
RUN groupadd -g 1000 posterflow && \
    useradd -u 1000 -g posterflow -m -s /bin/bash posterflow

# Set working directory
WORKDIR /app

# Copy requirements and install as root
COPY backend/requirements.txt .
RUN python -m pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Create necessary directories
RUN mkdir -p /config/logs /app/frontend/dist

# Copy backend application code
COPY --chown=posterflow:posterflow backend/ .

# Copy built frontend from frontend-builder stage
COPY --from=frontend-builder --chown=posterflow:posterflow /frontend/dist /app/frontend/dist

# Copy and configure entrypoint script
COPY --chmod=755 entrypoint.sh /entrypoint.sh

# Expose port (both frontend and backend on same port now)
EXPOSE 8000

# Container starts as root; entrypoint remaps PUID/PGID then drops to posterflow user
ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "warning", "--no-access-log", "--timeout-graceful-shutdown", "3"]
