#!/usr/bin/env bash
set -euo pipefail

# Deploy script for the notes workspace.
# Handles building, saving, uploading, and starting the Docker container.
# Checks the public endpoint, prints diagnostics on failure, and notifies
# via scripts/notify.sh on either success or failure.

REMOTE_HOST="test.k3rnel-pan1c.com"
REMOTE_PORT=2223
REMOTE_USER="marko"
IMAGE_NAME="notes"
REMOTE="$REMOTE_USER@$REMOTE_HOST"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Persistent SSH multiplexed connection — all ssh/scp commands share one TCP
# session. Force the control dir under /tmp: Unix domain sockets cap at ~104
# bytes, and macOS's TMPDIR (/var/folders/...) plus the %C hash exceeds that
# limit.
SSH_CONTROL_DIR=$(mktemp -d /tmp/${IMAGE_NAME}-deploy-ssh.XXXXXX)
SSH_CONTROL_PATH="$SSH_CONTROL_DIR/ctrl-%C"
SSH_OPTS=(-p "$REMOTE_PORT" -o ConnectTimeout=10 -o ServerAliveInterval=5 -o ServerAliveCountMax=12 -o ControlMaster=auto -o ControlPath="$SSH_CONTROL_PATH" -o ControlPersist=300)
SCP_OPTS=(-P "$REMOTE_PORT" -o ConnectTimeout=10 -o ServerAliveInterval=5 -o ServerAliveCountMax=12 -o ControlMaster=auto -o ControlPath="$SSH_CONTROL_PATH" -o ControlPersist=300)

# Tracked by the EXIT trap so failure notifications can name the failing
# step. Updated as the script progresses.
deploy_step="initialization"

cleanup_ssh() {
  ssh "${SSH_OPTS[@]}" -O exit "$REMOTE" 2>/dev/null || true
  rm -rf "$SSH_CONTROL_DIR"
}

notify_deploy_result() {
  local status="$1"  # "succeeded" or "failed"
  local short_sha
  short_sha=$(git rev-parse --short HEAD 2>/dev/null || echo "?")

  # Derive the GitHub URL from `git remote get-url origin` so the commit
  # link doesn't hardcode the repo. Handles both git@ and https:// remotes.
  local repo_url=""
  if remote=$(git remote get-url origin 2>/dev/null); then
    repo_url="${remote%.git}"
    repo_url="${repo_url/git@github.com:/https://github.com/}"
  fi

  local app_link="[${IMAGE_NAME}](${PUBLIC_URL:-#})"
  local commit_link="${short_sha}"
  if [ -n "$repo_url" ] && [ "$short_sha" != "?" ]; then
    commit_link="[${short_sha}](${repo_url}/commit/${short_sha})"
  fi

  local subject
  if [ "$status" = "succeeded" ]; then
    subject="✅ ${app_link}: deployed ${commit_link}"
  else
    subject="❌ ${app_link}: deploy FAILED at \`${deploy_step}\` (${commit_link})"
  fi
  "${SCRIPT_DIR}/notify.sh" "$subject" || true
}

on_exit() {
  local exit_code=$?
  if [ "$exit_code" -eq 0 ]; then
    notify_deploy_result succeeded
  else
    notify_deploy_result failed
  fi
  cleanup_ssh
  exit "$exit_code"
}
trap on_exit EXIT

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

# Claude Code auth in production:
#   1. ANTHROPIC_API_KEY env var (optional override) → API-key billing
#   2. Otherwise: subscription credentials from the deploy host's
#      ~/.claude/ are seeded once into ~/.notes/claude-config/ on the
#      first deploy and the long-running container reads them via the
#      bind mount. The remote host MUST have Claude Code installed and
#      logged in (`npm install -g @anthropic-ai/claude-code` then
#      `claude /login` on the remote) before the first deploy will
#      succeed.
ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}"

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
deploy_step="build image"
printf "==> building image (%s, linux/amd64)..." "$IMAGE_NAME"
if [ "${SKIP_DOCKER_BUILD:-0}" != "1" ]; then
  ./scripts/build.sh linux/amd64 > /dev/null 2>&1
fi
echo "ok"

# ---- save & upload image -------------------------------------------------
deploy_step="save image"
printf "==> saving image..."
docker save "$IMAGE_NAME" | gzip > /tmp/"${IMAGE_NAME}".tar.gz
echo "ok"

deploy_step="upload image to remote"
printf "==> uploading to %s..." "$REMOTE_HOST"
retry_cmd 3 2 scp "${SCP_OPTS[@]}" /tmp/"${IMAGE_NAME}".tar.gz "$REMOTE":/tmp/"${IMAGE_NAME}".tar.gz
rm /tmp/"${IMAGE_NAME}".tar.gz
echo "ok"

deploy_step="load image on remote"
printf "==> loading image on remote..."
ssh "${SSH_OPTS[@]}" "$REMOTE" "
  docker load < /tmp/${IMAGE_NAME}.tar.gz
  rm /tmp/${IMAGE_NAME}.tar.gz
" > /dev/null 2>&1
echo "ok"

# ---- upload compose file -------------------------------------------------
deploy_step="upload compose file"
printf "==> uploading compose file..."
retry_cmd 3 2 ssh "${SSH_OPTS[@]}" "$REMOTE" "mkdir -p ~/${IMAGE_NAME}"
retry_cmd 3 2 scp "${SCP_OPTS[@]}" docker-compose.yml "$REMOTE":~/"${IMAGE_NAME}"/docker-compose.yml
echo "ok"

# ---- ensure data dirs on remote ------------------------------------------
# docker-compose.yml bind-mounts ~/.notes/data -> /data (pages, app state)
# and ~/.notes/qdrant -> /qdrant/storage (vector index).
#
# Docker auto-creates bind-mount source dirs as root if they don't exist,
# which then blocks subsequent non-root chmod/chown. We use sudo to force
# the dirs to our user and grant broad write perms so the in-container
# app user (and the qdrant container user) can both write.
deploy_step="ensure remote data dirs"
printf "==> ensuring remote data dirs..."
ensure_dirs_log=$(mktemp)
if ! ssh "${SSH_OPTS[@]}" "$REMOTE" '
  set -e
  sudo -n mkdir -p "$HOME/.notes/data" "$HOME/.notes/qdrant" "$HOME/.notes/claude-config"
  sudo -n chown -R "$USER:$USER" "$HOME/.notes"
  chmod -R a+rwX "$HOME/.notes"
' >"$ensure_dirs_log" 2>&1; then
  echo "FAIL"
  echo "    ensure-dirs output:"
  sed 's/^/    /' "$ensure_dirs_log"
  rm -f "$ensure_dirs_log"
  exit 1
fi
rm -f "$ensure_dirs_log"
echo "ok"

# ---- seed claude credentials --------------------------------------------
# On first deploy, copy the remote host's ~/.claude/ (where the operator
# has run `claude /login`) into ~/.notes/claude-config/, the dir that's
# bind-mounted into the container at /home/appuser/.claude. After the
# first seed the container manages its own credentials and refreshes
# tokens independently of the host's ~/.claude/.
#
# Skipped entirely if ANTHROPIC_API_KEY is set (the container uses
# the API key instead).
#
# Exit codes from the remote script:
#   0  already seeded OR just seeded successfully
#   2  no credentials in target AND no credentials on host
deploy_step="seed claude credentials"
if [ -z "$ANTHROPIC_API_KEY" ]; then
  printf "==> seeding claude credentials (first deploy only)..."
  set +e
  ssh "${SSH_OPTS[@]}" "$REMOTE" '
    set -e
    TARGET="$HOME/.notes/claude-config"
    SOURCE="$HOME/.claude"
    mkdir -p "$TARGET"
    if [ -f "$TARGET/.credentials.json" ]; then
      echo "  already seeded"
      exit 0
    fi
    if [ ! -f "$SOURCE/.credentials.json" ]; then
      exit 2
    fi
    # First-time copy: bring in credentials + minimal settings, owned
    # by the deploy user (uid 1000) so the container appuser can read
    # and write them. Exclude per-project history and caches.
    cp "$SOURCE/.credentials.json" "$TARGET/.credentials.json"
    [ -f "$SOURCE/settings.json" ] && cp "$SOURCE/settings.json" "$TARGET/settings.json"
    chmod 600 "$TARGET/.credentials.json"
    echo "  seeded from ~/.claude/"
  '
  rc=$?
  set -e
  if [ "$rc" -eq 2 ]; then
    echo "FAIL"
    echo "    Claude Code is not authenticated on $REMOTE_HOST." >&2
    echo "    SSH in once and run:" >&2
    echo "      npm install -g @anthropic-ai/claude-code" >&2
    echo "      claude /login" >&2
    echo "    then re-run ./scripts/deploy.sh" >&2
    exit 1
  elif [ "$rc" -ne 0 ]; then
    echo "FAIL (ssh exit $rc)"
    exit 1
  fi
  echo " ok"
fi

# ---- write .env on remote ------------------------------------------------
# All values are written through `printf %q` so secrets with quotes / spaces /
# special characters survive the heredoc. The .env file format docker-compose
# reads is documented here: https://docs.docker.com/compose/environment-variables/env-file/
deploy_step="write remote .env"
printf "==> writing remote .env..."
printf -v port_q '%q'             "$PORT"
printf -v llm_base_url_q '%q'     "$LLM_BASE_URL"
printf -v llm_api_key_q '%q'      "$LLM_API_KEY"
printf -v api_key_q '%q'          "$API_KEY"
printf -v auth_password_q '%q'    "$AUTH_PASSWORD"

# Optional values — only written if they're non-empty in the local shell.
# ANTHROPIC_API_KEY is optional: when unset, claude code in the container
# falls back to the OAuth credentials in the bind-mounted ~/.claude/.
# Writing an empty value would override the file-based auth, so we
# really do skip the line entirely instead of writing ANTHROPIC_API_KEY=.
extra_env=""
for var in LLM_MODEL STREAM_PACE_SECONDS ANTHROPIC_API_KEY; do
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
NOTES_EDITOR=claude
${extra_env}ENVEOF
EOF
echo "ok"

# ---- start services ------------------------------------------------------
deploy_step="remove stray containers"
# Remove stray containers that would block `docker compose up`:
#   1. Any container literally named `${IMAGE_NAME}` that isn't part of
#      the current compose project.
#   2. Legacy containers from earlier deploys with different names
#      (e.g. `bootstrap-template`).
#   3. Anything (not in our compose project) still binding the target PORT.
# The last one is the robust fallback — it catches cases where the legacy
# container was renamed or we don't know its old name.
printf "==> removing stray containers (if any)..."
ssh "${SSH_OPTS[@]}" "$REMOTE" "
  set +e
  # (1) + (2): well-known legacy names
  for name in ${IMAGE_NAME} bootstrap-template; do
    if docker inspect \"\$name\" >/dev/null 2>&1; then
      project_label=\$(docker inspect \"\$name\" --format '{{ index .Config.Labels \"com.docker.compose.project\" }}')
      if [ -z \"\$project_label\" ] || [ \"\$project_label\" != \"${IMAGE_NAME}\" ]; then
        docker rm -f \"\$name\" >/dev/null
      fi
    fi
  done
  # (3): anything binding PORT that isn't ours
  for cid in \$(docker ps -a --filter publish=${PORT} --format '{{.ID}}'); do
    project_label=\$(docker inspect \"\$cid\" --format '{{ index .Config.Labels \"com.docker.compose.project\" }}' 2>/dev/null)
    if [ -z \"\$project_label\" ] || [ \"\$project_label\" != \"${IMAGE_NAME}\" ]; then
      docker rm -f \"\$cid\" >/dev/null
    fi
  done
  exit 0
" 2>/dev/null || true
echo "ok"

deploy_step="start services"
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
deploy_step="wait for server"
printf "==> waiting for server..."
WAIT_TIMEOUT_SECONDS="${WAIT_TIMEOUT_SECONDS:-120}"
WAIT_INTERVAL_SECONDS="${WAIT_INTERVAL_SECONDS:-2}"
WAIT_DEADLINE=$(( $(date +%s) + WAIT_TIMEOUT_SECONDS ))
server_ready=false

while (( $(date +%s) < WAIT_DEADLINE )); do
  # /login returns 200 in both auth modes (password page or redirect) and
  # is not gated by API_KEY, so it's a safe liveness probe.
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
deploy_step="check public endpoint"
printf "==> checking public endpoint (%s)..." "$PUBLIC_URL"
if ! body=$(curl -sfL --max-time 10 "$PUBLIC_URL"); then
  echo "FAIL"
  echo "    could not reach $PUBLIC_URL"
  exit 1
fi

if ! echo "$body" | grep -qE "<title>Notes</title>|Sign in"; then
  echo "FAIL"
  echo "    $PUBLIC_URL response did not look right"
  echo "    $body"
  exit 1
fi
echo "ok"

./scripts/get_logs.sh

echo "==> deployed $IMAGE_NAME to $PUBLIC_URL"
