FROM python:3.13.13-slim@sha256:aa938a849bcb82dce8f49480f056ab82bf5c1c3ebc294f0430f37b6820e7f286

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

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
