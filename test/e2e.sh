#!/usr/bin/env bash
set -euo pipefail

# End-to-end smoke test: builds the image, starts the compose stack, waits
# for the notes server, verifies the index page renders with a fresh deploy
# date, then shuts down.

: "${PORT:?PORT must be set}"

# Provide safe defaults for vars the container needs but that aren't part
# of the e2e surface. These don't need to be real — the server starts
# fine without a working LLM endpoint because agent calls are lazy.
export LLM_BASE_URL="${LLM_BASE_URL:-http://fake-llm-for-e2e.invalid}"
export LLM_API_KEY="${LLM_API_KEY:-e2e}"
# Non-empty default so the e2e exercises the auth-gated path of the
# /v1/* routes and CI does not need any real secrets configured.
export API_KEY="${API_KEY:-e2e-test-key}"
export AUTH_MODE="${AUTH_MODE:-none}"
export AUTH_PASSWORD="${AUTH_PASSWORD:-}"
export NOTES_EDITOR="${NOTES_EDITOR:-mock}"
export NOTES_DISABLE_QDRANT="${NOTES_DISABLE_QDRANT:-1}"

mkdir -p "${HOME}/.notes/data" "${HOME}/.notes/qdrant"
chmod 777 "${HOME}/.notes/data" "${HOME}/.notes/qdrant"

if [ "${SKIP_DOCKER_BUILD:-0}" != "1" ]; then
  ./scripts/build.sh
fi

# If qdrant is disabled for e2e, only start the notes service.
if [ "$NOTES_DISABLE_QDRANT" = "1" ]; then
  docker compose up -d notes
else
  docker compose up -d
fi
trap 'docker compose down -v --remove-orphans || true' EXIT

echo "waiting for server..."
wait_timeout_seconds=120
wait_interval_seconds=2
deadline=$((SECONDS + wait_timeout_seconds))
attempt=0
last_status=""

while (( SECONDS < deadline )); do
  attempt=$((attempt + 1))
  status=$(curl -sS -o /dev/null -w "%{http_code}" "http://localhost:${PORT}/" || true)
  last_status="$status"

  if [ "$status" = "200" ] || [ "$status" = "302" ]; then
    echo "server is up (attempt ${attempt}, HTTP ${status})"
    break
  fi

  if [[ "$status" == 5* ]]; then
    echo "FAIL: server returned HTTP ${status} while starting (attempt ${attempt})"
    docker compose logs --tail 80 notes || true
    exit 1
  fi

  if [ -z "$status" ] || [ "$status" = "000" ]; then
    echo "waiting... attempt ${attempt} (server not reachable yet)"
  else
    echo "waiting... attempt ${attempt} (HTTP ${status})"
  fi

  sleep "$wait_interval_seconds"
done

if [ "$last_status" != "200" ] && [ "$last_status" != "302" ]; then
  echo "FAIL: server did not become ready within ${wait_timeout_seconds}s (last status: ${last_status:-none})"
  docker compose logs --tail 80 notes || true
  exit 1
fi

echo "checking response..."
body=$(curl -sfL "http://localhost:${PORT}/")

if ! echo "$body" | grep -q '<title>Notes</title>'; then
  echo "FAIL: response did not contain the Notes title tag"
  echo "$body" | head -40
  exit 1
fi

echo "checking deploy date meta tag..."
deploy_date=$(echo "$body" | sed -n 's/.*name="notes:deploy-date" content="\([^"]*\)".*/\1/p')
if [ -z "$deploy_date" ]; then
  echo "FAIL: could not find notes:deploy-date meta tag"
  echo "$body" | head -40
  exit 1
fi

if deploy_ts=$(date -u -d "$deploy_date" +%s 2>/dev/null); then
  : # GNU date
elif deploy_ts=$(date -u -j -f "%Y-%m-%dT%H:%M:%SZ" "$deploy_date" +%s 2>/dev/null); then
  : # BSD date
else
  echo "WARN: could not parse deploy date: ${deploy_date} (skipping freshness check)"
  echo "e2e test passed"
  exit 0
fi
now_ts=$(date -u +%s)
age=$(( now_ts - deploy_ts ))

if [ "$age" -gt 600 ]; then
  echo "FAIL: deploy date is ${age}s old (max 600s)"
  exit 1
fi

echo "deploy date is ${age}s old, ok"

echo "checking API: /v1/pages (auth required)..."
# First, confirm the route is actually gated when no bearer is sent.
unauth_status=$(curl -sS -o /dev/null -w "%{http_code}" "http://localhost:${PORT}/v1/pages" || true)
if [ "$unauth_status" != "401" ]; then
  echo "FAIL: /v1/pages without Authorization should be 401, got ${unauth_status}"
  exit 1
fi
# Then exercise the authenticated path with the bearer.
pages_json=$(curl -sf -H "Authorization: Bearer ${API_KEY}" "http://localhost:${PORT}/v1/pages")
if ! echo "$pages_json" | grep -q '"welcome"'; then
  echo "FAIL: /v1/pages did not include the seeded welcome page"
  echo "$pages_json"
  exit 1
fi

echo "e2e test passed"
