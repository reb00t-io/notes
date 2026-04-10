#!/usr/bin/env bash
set -euo pipefail

# Deploy script for bootstrap-template
# This script handles building, saving, uploading, and starting the Docker container.
# It also checks the public endpoint and prints diagnostics on failure.

REMOTE_HOST="test.k3rnel-pan1c.com"
REMOTE_PORT=2223
REMOTE_USER="marko"
IMAGE_NAME="notes"
REMOTE="$REMOTE_USER@$REMOTE_HOST"

# Persistent SSH multiplexed connection — all ssh/scp commands share one TCP
# session. Force the control dir under /tmp: Unix domain sockets cap at ~104
# bytes, and macOS's TMPDIR (/var/folders/...) plus the %C hash exceeds that
# limit.
SSH_CONTROL_DIR=$(mktemp -d /tmp/${IMAGE_NAME}-deploy-ssh.XXXXXX)
SSH_CONTROL_PATH="$SSH_CONTROL_DIR/ctrl-%C"
SSH_OPTS=(-p "$REMOTE_PORT" -o ConnectTimeout=10 -o ServerAliveInterval=5 -o ServerAliveCountMax=12 -o ControlMaster=auto -o ControlPath="$SSH_CONTROL_PATH" -o ControlPersist=300)
SCP_OPTS=(-P "$REMOTE_PORT" -o ConnectTimeout=10 -o ServerAliveInterval=5 -o ServerAliveCountMax=12 -o ControlMaster=auto -o ControlPath="$SSH_CONTROL_PATH" -o ControlPersist=300)

cleanup_ssh() {
  ssh "${SSH_OPTS[@]}" -O exit "$REMOTE" 2>/dev/null || true
  rm -rf "$SSH_CONTROL_DIR"
}
trap cleanup_ssh EXIT

# Retry wrapper: retry_cmd <max_attempts> <backoff_secs> <command...>
retry_cmd() {
  local max=$1 backoff=$2; shift 2
  local attempt=1
  while true; do
    if "$@"; then return 0; fi
    if (( attempt >= max )); then return 1; fi
    echo " (attempt $attempt/$max failed, retrying in ${backoff}s...)"
    sleep "$backoff"
    backoff=$(( backoff * 2 ))
    attempt=$(( attempt + 1 ))
  done
}

# ---- required environment ------------------------------------------------
: "${PORT:?PORT must be set}"
: "${PUBLIC_URL:?PUBLIC_URL must be set}"
: "${LLM_BASE_URL:?LLM_BASE_URL must be set}"
: "${LLM_API_KEY:?LLM_API_KEY must be set}"
: "${API_KEY:?API_KEY must be set}"
: "${AUTH_PASSWORD:?AUTH_PASSWORD must be set}"

print_remote_diagnostics() {
  echo "    remote diagnostics:"
  ssh "${SSH_OPTS[@]}" "$REMOTE" "
    set +e
    cd ~/${IMAGE_NAME} 2>/dev/null || true
    echo '--- docker compose ps ---'
    docker compose ps 2>&1 || true
    echo
    echo '--- container state ---'
    docker inspect ${IMAGE_NAME} --format '{{json .State}}' 2>&1 || true
    echo
    echo '--- container logs (stdout + stderr, last 200 lines) ---'
    docker compose logs --tail 200 2>&1 || docker logs --tail 200 ${IMAGE_NAME} 2>&1 || true
  " || true
}

# ---- build ---------------------------------------------------------------
printf "==> building image (%s, linux/amd64)..." "$IMAGE_NAME"
if [ "${SKIP_DOCKER_BUILD:-0}" != "1" ]; then
  ./scripts/build.sh linux/amd64 > /dev/null 2>&1
fi
echo "ok"

# ---- save & upload image -------------------------------------------------
printf "==> saving image..."
docker save "$IMAGE_NAME" | gzip > /tmp/"${IMAGE_NAME}".tar.gz
echo "ok"

printf "==> uploading to %s..." "$REMOTE_HOST"
retry_cmd 3 2 scp "${SCP_OPTS[@]}" /tmp/"${IMAGE_NAME}".tar.gz "$REMOTE":/tmp/"${IMAGE_NAME}".tar.gz
rm /tmp/"${IMAGE_NAME}".tar.gz
echo "ok"

printf "==> loading image on remote..."
ssh "${SSH_OPTS[@]}" "$REMOTE" "
  docker load < /tmp/${IMAGE_NAME}.tar.gz
  rm /tmp/${IMAGE_NAME}.tar.gz
" > /dev/null 2>&1
echo "ok"

# ---- upload compose file -------------------------------------------------
printf "==> uploading compose file..."
retry_cmd 3 2 ssh "${SSH_OPTS[@]}" "$REMOTE" "mkdir -p ~/${IMAGE_NAME}"
retry_cmd 3 2 scp "${SCP_OPTS[@]}" docker-compose.yml "$REMOTE":~/"${IMAGE_NAME}"/docker-compose.yml
echo "ok"

# ---- ensure data dir on remote -------------------------------------------
printf "==> ensuring remote data dir..."
ssh "${SSH_OPTS[@]}" "$REMOTE" 'mkdir -p "$HOME/.bootstrap-template/data"' > /dev/null 2>&1
echo "ok"

# ---- write .env on remote ------------------------------------------------
# All values are written through `printf %q` so secrets with quotes / spaces /
# special characters survive the heredoc. The .env file format docker-compose
# reads is documented here: https://docs.docker.com/compose/environment-variables/env-file/
printf "==> writing remote .env..."
printf -v port_q '%q'           "$PORT"
printf -v llm_base_url_q '%q'   "$LLM_BASE_URL"
printf -v llm_api_key_q '%q'    "$LLM_API_KEY"
printf -v api_key_q '%q'        "$API_KEY"
printf -v auth_password_q '%q'  "$AUTH_PASSWORD"

# Optional values — only written if they're non-empty in the local shell.
extra_env=""
for var in LLM_MODEL STREAM_PACE_SECONDS; do
  if [[ -n "${!var:-}" ]]; then
    printf -v val_q '%q' "${!var}"
    extra_env+="${var}=${val_q}"$'\n'
  fi
done

retry_cmd 3 2 ssh "${SSH_OPTS[@]}" "$REMOTE" 'bash -se' <<EOF
cat > ~/${IMAGE_NAME}/.env <<'ENVEOF'
PORT=$port_q
LLM_BASE_URL=$llm_base_url_q
LLM_API_KEY=$llm_api_key_q
API_KEY=$api_key_q
AUTH_MODE=password
AUTH_PASSWORD=$auth_password_q
${extra_env}ENVEOF
EOF
echo "ok"

# ---- start services ------------------------------------------------------
# Remove any stray container with the target name that isn't managed by the
# current compose project (e.g. left over from an older `docker run`-based
# deploy). docker compose up would otherwise fail with a name conflict.
printf "==> removing stray container (if any)..."
ssh "${SSH_OPTS[@]}" "$REMOTE" "
  if docker inspect ${IMAGE_NAME} >/dev/null 2>&1; then
    project_label=\$(docker inspect ${IMAGE_NAME} --format '{{ index .Config.Labels \"com.docker.compose.project\" }}')
    if [ -z \"\$project_label\" ] || [ \"\$project_label\" != \"${IMAGE_NAME}\" ]; then
      docker rm -f ${IMAGE_NAME} >/dev/null
    fi
  fi
" 2>/dev/null || true
echo "ok"

printf "==> starting services..."
compose_up_log=$(mktemp)
if ! retry_cmd 3 4 ssh "${SSH_OPTS[@]}" "$REMOTE" "
  cd ~/${IMAGE_NAME}
  docker compose up -d --remove-orphans
" >"$compose_up_log" 2>&1; then
  echo "FAIL"
  echo "    docker compose up output:"
  sed 's/^/    /' "$compose_up_log"
  rm -f "$compose_up_log"
  print_remote_diagnostics
  exit 1
fi
rm -f "$compose_up_log"
echo "ok"

# ---- wait for server -----------------------------------------------------
printf "==> waiting for server..."
WAIT_TIMEOUT_SECONDS="${WAIT_TIMEOUT_SECONDS:-120}"
WAIT_INTERVAL_SECONDS="${WAIT_INTERVAL_SECONDS:-2}"
WAIT_DEADLINE=$(( $(date +%s) + WAIT_TIMEOUT_SECONDS ))
server_ready=false

while (( $(date +%s) < WAIT_DEADLINE )); do
  if ssh "${SSH_OPTS[@]}" "$REMOTE" "curl -sf --max-time 3 http://localhost:${PORT}/login > /dev/null" 2>/dev/null; then
    server_ready=true
    break
  fi
  sleep "$WAIT_INTERVAL_SECONDS"
done

if [[ "$server_ready" != true ]]; then
  echo "FAIL"
  echo "    server did not start within ${WAIT_TIMEOUT_SECONDS}s"
  print_remote_diagnostics
  exit 1
fi
echo "ok"

# ---- public smoke check --------------------------------------------------
printf "==> checking public endpoint (%s)..." "$PUBLIC_URL"
if ! body=$(curl -sfL --max-time 10 "$PUBLIC_URL"); then
  echo "FAIL"
  echo "    could not reach $PUBLIC_URL"
  exit 1
fi

if ! echo "$body" | grep -qE "hello|Sign in"; then
  echo "FAIL"
  echo "    $PUBLIC_URL response did not look right"
  echo "    $body"
  exit 1
fi
echo "ok"

./scripts/get_logs.sh

echo "==> deployed $IMAGE_NAME to $PUBLIC_URL"
