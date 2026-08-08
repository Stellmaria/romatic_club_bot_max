#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${ROMATIC_APP_DIR:-/srv/romatic-club-max}"
ENV_FILE="${ROMATIC_ENV_FILE:-.env}"
WEBAPP_ENV_FILE="${WEBAPP_ENV_FILE:-.env.webapp}"
HEALTH_ATTEMPTS="${WEBAPP_HEALTH_ATTEMPTS:-30}"
HEALTH_INTERVAL="${WEBAPP_HEALTH_INTERVAL:-2}"

cd "$APP_DIR"

for required_file in "$ENV_FILE" "$WEBAPP_ENV_FILE" compose.yaml compose.webapp.yaml; do
  if [[ ! -f "$required_file" ]]; then
    echo "Missing $APP_DIR/$required_file" >&2
    exit 2
  fi
done
if ! [[ "$HEALTH_ATTEMPTS" =~ ^[1-9][0-9]*$ ]]; then
  echo "WEBAPP_HEALTH_ATTEMPTS must be a positive integer." >&2
  exit 2
fi
if ! [[ "$HEALTH_INTERVAL" =~ ^[1-9][0-9]*$ ]]; then
  echo "WEBAPP_HEALTH_INTERVAL must be a positive integer." >&2
  exit 2
fi

export COMPOSE_BAKE=false
export WEBAPP_ENV_FILE
compose=(
  docker compose
  --env-file "$ENV_FILE"
  -f compose.yaml
  -f compose.webapp.yaml
)

"${compose[@]}" config --quiet
"${compose[@]}" build webapp
"${compose[@]}" up -d webapp

container_id="$("${compose[@]}" ps -q webapp)"
if [[ -z "$container_id" ]]; then
  echo "Compose service was not created: webapp" >&2
  exit 1
fi

for ((attempt = 1; attempt <= HEALTH_ATTEMPTS; attempt++)); do
  status="$(docker inspect --format '{{.State.Status}}' "$container_id")"
  health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id")"
  case "$status" in
    restarting|exited|dead|removing)
      echo "webapp failed with container state: $status" >&2
      "${compose[@]}" logs --tail 200 webapp >&2 || true
      exit 1
      ;;
  esac
  if [[ "$health" == "healthy" ]]; then
    echo "Telegram Mini App service is healthy."
    exit 0
  fi
  if [[ "$health" == "unhealthy" ]]; then
    echo "webapp failed healthcheck" >&2
    "${compose[@]}" logs --tail 200 webapp >&2 || true
    exit 1
  fi
  sleep "$HEALTH_INTERVAL"
done

echo "webapp did not become healthy in time" >&2
"${compose[@]}" logs --tail 200 webapp >&2 || true
exit 1
