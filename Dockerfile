FROM python:3.13-slim

# System deps:
# - git: PageStore versions every edit in the pages repo
# - curl, ca-certificates, gnupg: needed by the nodesource setup script
# - nodejs: required by @anthropic-ai/claude-code (npm-distributed CLI)
# Then npm-install Claude Code globally so the editor can shell out to
# `claude` from the workspace agent. Requires ANTHROPIC_API_KEY at
# runtime; see docker-compose.yml.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       git curl ca-certificates gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @anthropic-ai/claude-code \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml VERSION ./
COPY config/ ./config/
COPY docs/ ./docs/
COPY src/ .
# agent_scripts/ ships outside src/ but src/pages/claude_editor.py
# imports ClaudeAgent from there, so it must be on the container's
# Python path.
COPY agent_scripts/ ./agent_scripts/
RUN pip install --no-cache-dir .

ARG DEPLOY_DATE=unknown
ENV DEPLOY_DATE=$DEPLOY_DATE
ARG PORT
ENV PORT=$PORT
# Production editor: Claude Code via ClaudeAgent. Requires
# ANTHROPIC_API_KEY at runtime. Override to `mock` only for tests / CI
# environments where the real CLI is not available.
ENV NOTES_EDITOR=claude

# Pin appuser to uid 1000 so bind-mounted files from the host
# (notably ~/.claude/, which carries the user's Claude Code subscription
# credentials when ANTHROPIC_API_KEY is not used) are readable inside
# the container without uid translation.
RUN useradd --create-home --uid 1000 appuser \
    && git config --system user.email "notes@container" \
    && git config --system user.name "notes" \
    && mkdir -p /home/appuser/.claude \
    && chown -R appuser:appuser /home/appuser/.claude
USER appuser

EXPOSE $PORT
CMD ["python", "main.py"]
