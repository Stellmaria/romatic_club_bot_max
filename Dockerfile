FROM python:3.13.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN useradd --create-home --uid 10001 appuser

COPY requirements.lock ./
RUN python -m pip install --no-deps -r requirements.lock \
    && python -m pip check

COPY --chown=appuser:appuser . .
RUN mkdir -p /app/var /app/backups && chown -R appuser:appuser /app

USER appuser

CMD ["python", "main.py"]
