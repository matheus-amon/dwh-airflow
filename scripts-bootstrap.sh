#!/usr/bin/env bash
#
# Bring the warehouse stack up on the VM that terraform/ created.
#
# Deliberately separate from Terraform: `terraform apply` provisions the machine, this
# deploys the application onto it. Collapsing the two means a failed apt-get or a failed
# `docker compose up` marks the instance as half-created and every retry recreates the VM.
#
# Usage: ./scripts-bootstrap.sh <public-ip>
set -euo pipefail

TARGET="${1:?usage: $0 <public-ip>}"
WORKDIR="${WORKDIR:-/opt/warehouse}"
GITHUB_ORG="${GITHUB_ORG:-matheus-amon}"
AIRFLOW_BRANCH="${AIRFLOW_BRANCH:-main}"

log() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

log "waiting for ssh"
for _ in $(seq 1 30); do
  if ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new "opc@${TARGET}" true 2>/dev/null; then
    break
  fi
  sleep 10
done

# Everything runs in one remote script. A here-doc over one ssh invocation, rather than a
# series, so a failure part-way leaves a single clear error rather than a half-applied state
# across several sessions.
ssh "opc@${TARGET}" bash -s -- "${WORKDIR}" "${GITHUB_ORG}" "${AIRFLOW_BRANCH}" <<'REMOTE'
set -euo pipefail
WORKDIR="$1"; ORG="$2"; BRANCH="$3"

log() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

log "installing docker"
sudo dnf install -y docker || sudo yum install -y docker
sudo systemctl enable --now docker
sudo usermod -aG docker opc

log "pulling the repositories"
sudo mkdir -p "${WORKDIR}"
sudo chown opc:opc "${WORKDIR}"
cd "${WORKDIR}"
git clone --depth 1 --branch "${BRANCH}" "https://github.com/${ORG}/dwh-config-local.git"
git clone --depth 1 --branch "${BRANCH}" "https://github.com/${ORG}/dwh-airflow.git"
git clone --depth 1 --branch "${BRANCH}" "https://github.com/${ORG}/dwh-dbt.git"

# All three now sit side by side, which is the layout docker-compose expects: it mounts the dbt
# project from ../dwh-dbt, relative to the airflow directory.

log "writing credentials"
# A generated password, not admin/admin. The UI is on a public IP when airflow_ui_port is set,
# and 'admin' is the first credential anyone tries.
AIRFLOW_PASSWORD="$(openssl rand -base64 24 | tr -d '/+=' | head -c 24)"
umask 077
cat > "${WORKDIR}/dwh-airflow/.env" <<ENV
_AIRFLOW_WWW_USER_USERNAME=admin
_AIRFLOW_WWW_USER_PASSWORD=${AIRFLOW_PASSWORD}
ENV

cp "${WORKDIR}/dwh-config-local/.env.telemetry.example" "${WORKDIR}/dwh-config-local/.env.telemetry"

log "building the airflow image"
cd "${WORKDIR}/dwh-airflow"
docker compose build

log "starting the stack"
docker compose up -d
docker compose exec -T airflow-scheduler airflow dags reserialize >/dev/null 2>&1 || true

log "generating the raw layer"
cd "${WORKDIR}/dwh-config-local"
set -a; . ./.env.telemetry; set +a
docker compose -f docker-compose.telemetry.yml up -d
python3 -m venv .venv 2>/dev/null || true
.venv/bin/pip install -q -e . 2>/dev/null || .venv/bin/pip install -q duckdb faker numpy pandas "psycopg[binary]"
.venv/bin/python -m src.telemetry_lab.cli generate --output-dir data/raw
.venv/bin/python -m src.telemetry_lab.cli load

cat <<SUMMARY

============================================================
  Stack is up.

  Airflow UI     http://${TARGET}:8080
  username       admin
  password       ${AIRFLOW_PASSWORD}

  Save that password now: it is not stored anywhere else.
============================================================
SUMMARY
REMOTE
