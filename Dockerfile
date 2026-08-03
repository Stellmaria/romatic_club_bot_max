FROM python:3.14.6-slim@sha256:cea0e6040540fb2b965b6e7fb5ffa00871e632eef63719f0ea54bca189ce14a6

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        libssl3t64=3.5.6-1~deb13u2 \
        openssl=3.5.6-1~deb13u2 \
        openssl-provider-legacy=3.5.6-1~deb13u2 \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 appuser

ARG REQUIREMENTS_LOCK=requirements/bot.lock
COPY ${REQUIREMENTS_LOCK} /tmp/requirements.lock
RUN python -m pip install --require-hashes --no-deps -r /tmp/requirements.lock \
    && python -m pip check \
    && rm -f /tmp/requirements.lock

COPY --chown=appuser:appuser . .
RUN mkdir -p /app/var /app/backups && chown -R appuser:appuser /app

USER appuser

CMD ["python", "main.py"]
