# hadolint global ignore=DL3008
FROM python:3.14-slim AS base

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Install system packages + Node.js for Claude Code CLI + git for self-updating
RUN apt-get update && apt-get install -y --no-install-recommends \
    nginx curl jq unzip nodejs npm git openssh-client \
    && rm -rf /var/lib/apt/lists/*

# Install Claude Code CLI
RUN npm install -g @anthropic-ai/claude-code@2.1.114

# Install 1Password CLI (multi-arch: works on both amd64 and arm64)
RUN ARCH=$(dpkg --print-architecture) \
    && curl -sSfL "https://cache.agilebits.com/dist/1P/op2/pkg/v2.30.3/op_linux_${ARCH}_v2.30.3.zip" \
       -o /tmp/op.zip \
    && unzip -o /tmp/op.zip -d /usr/local/bin/ op \
    && rm /tmp/op.zip \
    && chmod +x /usr/local/bin/op

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

# Configure nginx to run as non-root
RUN sed -i 's/user www-data;/user ansible;/' /etc/nginx/nginx.conf \
    && mkdir -p /var/log/nginx /var/lib/nginx/body /run \
    && chown -R ansible:ansible /var/log/nginx /var/lib/nginx /run /etc/nginx

WORKDIR /app

USER ansible

EXPOSE 8080

ENTRYPOINT ["/app/entrypoint.sh"]
