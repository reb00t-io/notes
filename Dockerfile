FROM python:3.13-slim

# git is required by src/pages/store.py for versioning the pages repo.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml VERSION ./
COPY config/ ./config/
COPY docs/ ./docs/
COPY src/ .
RUN pip install --no-cache-dir .
ARG DEPLOY_DATE=unknown
ENV DEPLOY_DATE=$DEPLOY_DATE
ARG PORT
ENV PORT=$PORT
ENV NOTES_EDITOR=mock

RUN useradd --create-home appuser \
    && git config --system user.email "notes@container" \
    && git config --system user.name "notes"
USER appuser

EXPOSE $PORT
CMD ["python", "main.py"]
