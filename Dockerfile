# hadolint global ignore=DL3008
# The digest is of the multi-arch index. Dependabot proposes a new digest when the tag gets a new build.
FROM python:3.14-slim@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f AS base

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Install system packages + Node.js for Claude Code CLI.
# No apt version pins (DL3008): Debian removes old versions from the mirror, so a pin
# breaks the next build. Debian stable gets security fixes, and the base digest pins the rest.
RUN apt-get update && apt-get install -y --no-install-recommends \
    nginx curl jq nodejs npm \
    && rm -rf /var/lib/apt/lists/*

# Install Claude Code CLI
RUN npm install -g @anthropic-ai/claude-code@2.1.114

# Install supercronic (cron for non-root containers).
# Change both SHA-256 values together with the version. A wrong value stops the build.
ARG SUPERCRONIC_SHA256_amd64=feefa310da569c81b99e1027b86b27b51e6ee9ab647747b49099645120cfc671
ARG SUPERCRONIC_SHA256_arm64=f1f8585c66de020fef494dd636058f99949d108f569fef00016a1c8b9eb145b3
RUN ARCH=$(dpkg --print-architecture) \
    && case "${ARCH}" in \
         amd64) SHA256="${SUPERCRONIC_SHA256_amd64}" ;; \
         arm64) SHA256="${SUPERCRONIC_SHA256_arm64}" ;; \
         *) echo "No supercronic SHA-256 for ${ARCH}" >&2; exit 1 ;; \
       esac \
    && curl -sSfL --connect-timeout 15 --max-time 300 \
       "https://github.com/aptible/supercronic/releases/download/v0.2.33/supercronic-linux-${ARCH}" \
       -o /usr/local/bin/supercronic \
    && echo "${SHA256}  /usr/local/bin/supercronic" | sha256sum -c - \
    && chmod +x /usr/local/bin/supercronic

# Install Python dependencies (copied separately for layer caching)
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --root-user-action=ignore -r /tmp/requirements.txt && rm /tmp/requirements.txt

# A named user for UID 1000, the UID that the compose files and the docs use for the volumes
RUN groupadd -g 1000 ansible && useradd -u 1000 -g 1000 -m ansible

# HOME=/tmp because the root file system is read-only at run time. The Claude CLI writes its config there.
# PYTHONUNBUFFERED=1 keeps Python output and shell output in their order in docker logs.
ENV HOME=/tmp PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

# The image holds the main nginx config. The process runs as UID 1000, so it keeps its pid and temp files in /tmp.
COPY nginx.conf /etc/nginx/nginx.conf

# The code is owned by root and is read-only for UID 1000. The .dockerignore allow list picks the files.
WORKDIR /app
COPY . /app

# A new named volume copies the owner of /data on first use, so the volume is writable with no init step.
RUN mkdir -p /data/site /config && chown 1000:1000 /data /data/site

# Numeric form of the user above, so the host can resolve it (DL3066)
USER 1000:1000

EXPOSE 8080

# The start period covers the first sync, which runs the Claude notes.
HEALTHCHECK --interval=60s --timeout=10s --start-period=15m --retries=3 \
    CMD ["python3", "/app/scripts/container_health.py"]

ENTRYPOINT ["/app/entrypoint.sh"]
