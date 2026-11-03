# hadolint global ignore=DL3008
FROM python:3.14-slim AS base

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Install system packages + Node.js for Claude Code CLI
RUN apt-get update && apt-get install -y --no-install-recommends \
    nginx curl jq nodejs npm \
    && rm -rf /var/lib/apt/lists/*

# Install Claude Code CLI
RUN npm install -g @anthropic-ai/claude-code@2.1.114

# Install supercronic (cron for non-root containers)
RUN ARCH=$(dpkg --print-architecture) \
    && curl -sSfL "https://github.com/aptible/supercronic/releases/download/v0.2.33/supercronic-linux-${ARCH}" \
       -o /usr/local/bin/supercronic \
    && chmod +x /usr/local/bin/supercronic

# Install Python dependencies (copied separately for layer caching)
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --root-user-action=ignore -r /tmp/requirements.txt && rm /tmp/requirements.txt

# Create ansible user (matches host UID/GID)
RUN groupadd -g 1000 ansible && useradd -u 1000 -g 1000 -m ansible

# HOME=/tmp because the root file system is read-only at run time. The Claude CLI writes its config there.
ENV HOME=/tmp PYTHONDONTWRITEBYTECODE=1

# The image holds the main nginx config. The process runs as UID 1000, so it keeps its pid and temp files in /tmp.
COPY nginx.conf /etc/nginx/nginx.conf

# The code is owned by root and is read-only for UID 1000. The .dockerignore allow list picks the files.
WORKDIR /app
COPY . /app

# A new named volume copies the owner of /data on first use, so the volume is writable with no init step.
RUN mkdir -p /data/site /config && chown 1000:1000 /data /data/site

# Numeric form of the ansible user created above, so the host can resolve it (DL3066)
USER 1000:1000

EXPOSE 8080

# The start period covers the first sync, which runs the Claude notes.
HEALTHCHECK --interval=60s --timeout=10s --start-period=15m --retries=3 \
    CMD ["python3", "/app/scripts/container_health.py"]

ENTRYPOINT ["/app/entrypoint.sh"]
