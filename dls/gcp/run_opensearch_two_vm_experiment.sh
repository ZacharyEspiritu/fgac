#!/usr/bin/env bash

# Copyright 2026 MongoDB
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

set -euo pipefail

usage() {
  cat <<'EOF'
Launch the OpenSearch search_as_you_type n-gram attack on two GCP VMs.

The script creates:
  - one database VM running single-node OpenSearch in Docker
  - one attacker VM running unfilter-dls enumerate

Both VMs are created in the same concrete us-central1 zone. By default the
attack process is started with nohup on the attacker VM and this launcher exits
after printing monitor/fetch commands.

Required:
  --project PROJECT_ID

Common options:
  --zone ZONE                  Concrete zone, default: us-central1-a
  --prefix NAME                Resource name prefix, default: mpp-exp-<timestamp>
  --machine-type TYPE          VM type for both VMs, default: c4-standard-16
  --d D                        Corpus size label, default: 100
  --corpus-file PATH           Local repo-relative JSONL file, default: dataset/enron_d<D>.jsonl
  --config PATH                Local repo-relative config file, default: config/config.yml
  --foreground                 Run the attack in the SSH session instead of nohup
  --iap                        Use gcloud --tunnel-through-iap for SSH/SCP
  --dry-run                    Print planned resources and exit

Advanced options:
  --network NAME               VPC name, default: <prefix>-net
  --subnet NAME                Subnet name, default: <prefix>-subnet
  --subnet-range CIDR          Subnet CIDR, default: 10.42.0.0/24
  --ssh-source-ranges CIDRS    SSH firewall source ranges, default: 0.0.0.0/0
  --boot-disk-size SIZE        Boot disk size for both VMs, default: 200GB
  --boot-disk-type TYPE        Boot disk type for both VMs, default: hyperdisk-balanced
  --opensearch-image IMAGE     OpenSearch Docker image, default: opensearchproject/opensearch:3.6.0
  --remote-dir NAME            Directory under $HOME on each VM, default: opensearch-experiment

Any arguments after -- are appended to the attack command. Later duplicate
attack flags generally override earlier argparse values.

Example:
  gcp/run_opensearch_two_vm_experiment.sh \
    --project my-gcp-project \
    --zone us-central1-a \
    --prefix reviewer-d1000 \
    --d 1000
EOF
}

die() {
  echo "error: $*" >&2
  exit 1
}

log() {
  echo "==> $*"
}

shell_join() {
  local arg
  printf '%q ' "$@"
  printf '\n'
}

shell_quote() {
  printf '%q' "$1"
}

emit_bash_array() {
  local name="$1"
  shift
  printf '%s=(\n' "$name"
  local arg
  for arg in "$@"; do
    printf '  %q\n' "$arg"
  done
  printf ')\n'
}

zone_region() {
  local zone="$1"
  printf '%s\n' "${zone%-*}"
}

PROJECT_ID="${PROJECT_ID:-}"
ZONE="${ZONE:-us-central1-a}"
PREFIX="${PREFIX:-mpp-exp-$(date +%Y%m%d-%H%M%S)}"
MACHINE_TYPE="${MACHINE_TYPE:-c4-standard-16}"
D="${D:-100}"
CORPUS_FILE="${CORPUS_FILE:-}"
CONFIG_FILE="${CONFIG_FILE:-config/config.yml}"
NETWORK="${NETWORK:-}"
SUBNET="${SUBNET:-}"
SUBNET_RANGE="${SUBNET_RANGE:-10.42.0.0/24}"
SSH_SOURCE_RANGES="${SSH_SOURCE_RANGES:-0.0.0.0/0}"
BOOT_DISK_SIZE="${BOOT_DISK_SIZE:-200GB}"
BOOT_DISK_TYPE="${BOOT_DISK_TYPE:-hyperdisk-balanced}"
OPENSEARCH_IMAGE="${OPENSEARCH_IMAGE:-opensearchproject/opensearch:3.6.0}"
REMOTE_DIR="${REMOTE_DIR:-opensearch-experiment}"
IMAGE_FAMILY="${IMAGE_FAMILY:-ubuntu-2404-lts-amd64}"
IMAGE_PROJECT="${IMAGE_PROJECT:-ubuntu-os-cloud}"
DETACH=1
USE_IAP=0
DRY_RUN=0
EXTRA_ATTACK_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project)
      PROJECT_ID="${2:?missing value for --project}"
      shift 2
      ;;
    --zone)
      ZONE="${2:?missing value for --zone}"
      shift 2
      ;;
    --prefix)
      PREFIX="${2:?missing value for --prefix}"
      shift 2
      ;;
    --machine-type)
      MACHINE_TYPE="${2:?missing value for --machine-type}"
      shift 2
      ;;
    --d)
      D="${2:?missing value for --d}"
      shift 2
      ;;
    --corpus-file)
      CORPUS_FILE="${2:?missing value for --corpus-file}"
      shift 2
      ;;
    --config)
      CONFIG_FILE="${2:?missing value for --config}"
      shift 2
      ;;
    --network)
      NETWORK="${2:?missing value for --network}"
      shift 2
      ;;
    --subnet)
      SUBNET="${2:?missing value for --subnet}"
      shift 2
      ;;
    --subnet-range)
      SUBNET_RANGE="${2:?missing value for --subnet-range}"
      shift 2
      ;;
    --ssh-source-ranges)
      SSH_SOURCE_RANGES="${2:?missing value for --ssh-source-ranges}"
      shift 2
      ;;
    --boot-disk-size)
      BOOT_DISK_SIZE="${2:?missing value for --boot-disk-size}"
      shift 2
      ;;
    --boot-disk-type)
      BOOT_DISK_TYPE="${2:?missing value for --boot-disk-type}"
      shift 2
      ;;
    --opensearch-image)
      OPENSEARCH_IMAGE="${2:?missing value for --opensearch-image}"
      shift 2
      ;;
    --remote-dir)
      REMOTE_DIR="${2:?missing value for --remote-dir}"
      shift 2
      ;;
    --foreground)
      DETACH=0
      shift
      ;;
    --iap)
      USE_IAP=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      EXTRA_ATTACK_ARGS=("$@")
      break
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

[[ -n "$PROJECT_ID" ]] || die "--project is required"
[[ "$ZONE" =~ ^us-central1-[a-z]$ ]] || die "--zone must be a concrete us-central1 zone, e.g. us-central1-a"
[[ "$PREFIX" =~ ^[a-z]([-a-z0-9]*[a-z0-9])?$ ]] || die "--prefix must be a valid lowercase GCE name prefix"
[[ "$REMOTE_DIR" != *"/"* ]] || die "--remote-dir must be a simple directory name under the remote user's home"

REGION="$(zone_region "$ZONE")"
DB_VM="${PREFIX}-db"
ATTACKER_VM="${PREFIX}-attacker"
DB_TAG="${PREFIX}-db"
ATTACKER_TAG="${PREFIX}-attacker"
SSH_TAG="${PREFIX}-ssh"
NETWORK="${NETWORK:-${PREFIX}-net}"
SUBNET="${SUBNET:-${PREFIX}-subnet}"
CORPUS_FILE="${CORPUS_FILE:-dataset/enron_d${D}.jsonl}"
RUN_ID="${PREFIX}-d${D}"
RESULT_DIR_REL="results/gcp/${RUN_ID}"
LOG_REL="${RESULT_DIR_REL}/opensearch_d${D}_sasy_ngram4.log"
STATS_REL="${RESULT_DIR_REL}/opensearch_d${D}_sasy_ngram4_stats.json"
PID_REL="${RESULT_DIR_REL}/attack.pid"
ADMIN_PASSWORD='@!A134Kwjdoiwna!'
USER_PASSWORD='KTrdMBtPB6NmUXP'

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REMOTE_REPO_PLACEHOLDER="__REMOTE_REPO__"
REMOTE_REPO_DISPLAY="\$HOME/${REMOTE_DIR}"
absolute_local_path() {
  local path="$1"
  case "${path}" in
    /*) printf '%s\n' "${path}" ;;
    *) printf '%s/%s\n' "${REPO_ROOT}" "${path}" ;;
  esac
}

CORPUS_FILE_ABS="$(absolute_local_path "${CORPUS_FILE}")"
CONFIG_FILE_ABS="$(absolute_local_path "${CONFIG_FILE}")"
LOCAL_FETCH_DIR="$(pwd -P)"
[[ -f "${CORPUS_FILE_ABS}" ]] || die "corpus file not found: ${CORPUS_FILE_ABS}"
[[ -f "${CONFIG_FILE_ABS}" ]] || die "config file not found: ${CONFIG_FILE_ABS}"
[[ -f "${REPO_ROOT}/src/cli.py" ]] || die "attack entry point not found: ${REPO_ROOT}/src/cli.py"
[[ -f "${REPO_ROOT}/src/util/build_enumerator_command.py" ]] || die "command builder not found: ${REPO_ROOT}/src/util/build_enumerator_command.py"

BUILD_ATTACK_CMD=(
  python3 "${REPO_ROOT}/src/util/build_enumerator_command.py"
  --config "${CONFIG_FILE_ABS}"
  --arguments-only
  --backend opensearch
  --corpus-file "${REMOTE_REPO_PLACEHOLDER}/${CORPUS_FILE}"
  --stats-file "${REMOTE_REPO_PLACEHOLDER}/${STATS_REL}"
  --random-seed "$(od -An -N8 -tu8 /dev/urandom | tr -d '[:space:]')"
  --progress-interval 30
)
if [[ ${#EXTRA_ATTACK_ARGS[@]} -gt 0 ]]; then
  for extra_arg in "${EXTRA_ATTACK_ARGS[@]}"; do
    BUILD_ATTACK_CMD+=(--extra-arg "$extra_arg")
  done
fi
ATTACK_ARGS=()
ATTACK_COMMAND_LINES="$("${BUILD_ATTACK_CMD[@]}")"
while IFS= read -r attack_arg; do
  [[ -n "$attack_arg" ]] && ATTACK_ARGS+=("$attack_arg")
done <<< "$ATTACK_COMMAND_LINES"

attack_args_for_display() {
  local display_args=()
  local arg

  for arg in "${ATTACK_ARGS[@]}"; do
    display_args+=("${arg//${REMOTE_REPO_PLACEHOLDER}/${REMOTE_REPO_DISPLAY}}")
  done
  shell_join "${display_args[@]}"
}

GCLOUD=(gcloud --project "$PROJECT_ID")
SSH_FLAGS=(--zone "$ZONE")
SCP_FLAGS=(--zone "$ZONE")
if [[ "$USE_IAP" -eq 1 ]]; then
  SSH_FLAGS+=(--tunnel-through-iap)
  SCP_FLAGS+=(--tunnel-through-iap)
fi

print_plan() {
  cat <<EOF
Project:        ${PROJECT_ID}
Zone:           ${ZONE}
Region:         ${REGION}
Machine type:   ${MACHINE_TYPE}
Database VM:    ${DB_VM}
Attacker VM:    ${ATTACKER_VM}
Network/subnet: ${NETWORK} / ${SUBNET} (${SUBNET_RANGE})
Corpus file:    ${CORPUS_FILE_ABS}
Config file:    ${CONFIG_FILE_ABS}
Remote dir:     ${REMOTE_REPO_DISPLAY}
OpenSearch:     ${OPENSEARCH_IMAGE} single-node
Boot disk:      ${BOOT_DISK_SIZE} ${BOOT_DISK_TYPE}
Detached:       ${DETACH}
Stats path:     ${REMOTE_REPO_DISPLAY}/${STATS_REL}
Log path:       ${REMOTE_REPO_DISPLAY}/${LOG_REL}

Attack command:
unfilter-dls enumerate $(attack_args_for_display)
EOF
}

if [[ "$DRY_RUN" -eq 1 ]]; then
  print_plan
  exit 0
fi

run() {
  echo "+ $(shell_join "$@")"
  "$@"
}

resource_exists() {
  "$@" >/dev/null 2>&1
}

ensure_network() {
  if resource_exists "${GCLOUD[@]}" compute networks describe "$NETWORK"; then
    log "network exists: $NETWORK"
  else
    run "${GCLOUD[@]}" compute networks create "$NETWORK" --subnet-mode=custom
  fi

  if resource_exists "${GCLOUD[@]}" compute networks subnets describe "$SUBNET" --region "$REGION"; then
    log "subnet exists: $SUBNET"
  else
    run "${GCLOUD[@]}" compute networks subnets create "$SUBNET" \
      --network "$NETWORK" \
      --region "$REGION" \
      --range "$SUBNET_RANGE"
  fi

  if resource_exists "${GCLOUD[@]}" compute firewall-rules describe "${PREFIX}-allow-ssh"; then
    log "firewall rule exists: ${PREFIX}-allow-ssh"
  else
    run "${GCLOUD[@]}" compute firewall-rules create "${PREFIX}-allow-ssh" \
      --network "$NETWORK" \
      --allow tcp:22 \
      --source-ranges "$SSH_SOURCE_RANGES" \
      --target-tags "$SSH_TAG"
  fi

  if resource_exists "${GCLOUD[@]}" compute firewall-rules describe "${PREFIX}-allow-opensearch-attacker"; then
    log "firewall rule exists: ${PREFIX}-allow-opensearch-attacker"
  else
    run "${GCLOUD[@]}" compute firewall-rules create "${PREFIX}-allow-opensearch-attacker" \
      --network "$NETWORK" \
      --allow tcp:9200 \
      --source-tags "$ATTACKER_TAG" \
      --target-tags "$DB_TAG"
  fi
}

ensure_instance() {
  local name="$1"
  local tags="$2"
  if resource_exists "${GCLOUD[@]}" compute instances describe "$name" --zone "$ZONE"; then
    log "instance exists: $name"
  else
    run "${GCLOUD[@]}" compute instances create "$name" \
      --zone "$ZONE" \
      --machine-type "$MACHINE_TYPE" \
      --network "$NETWORK" \
      --subnet "$SUBNET" \
      --tags "$tags" \
      --image-family "$IMAGE_FAMILY" \
      --image-project "$IMAGE_PROJECT" \
      --boot-disk-size "$BOOT_DISK_SIZE" \
      --boot-disk-type "$BOOT_DISK_TYPE" \
      --metadata enable-oslogin=FALSE
  fi
}

wait_for_ssh() {
  local vm="$1"
  log "waiting for SSH on $vm"
  for _ in $(seq 1 60); do
    if "${GCLOUD[@]}" compute ssh "$vm" "${SSH_FLAGS[@]}" --command "true" >/dev/null 2>&1; then
      return 0
    fi
    sleep 5
  done
  die "timed out waiting for SSH on $vm"
}

TMP_DIR="$(mktemp -d)"
ARCHIVE="${TMP_DIR}/opensearch-experiment.tgz"
DB_REMOTE_SCRIPT="${TMP_DIR}/setup_db_vm.sh"
ATTACKER_REMOTE_SCRIPT="${TMP_DIR}/setup_attacker_vm.sh"
trap 'rm -rf "$TMP_DIR"' EXIT

print_plan

log "creating repo archive"
COPYFILE_DISABLE=1 tar --no-xattrs -czf "$ARCHIVE" \
  --exclude '.git' \
  --exclude '.DS_Store' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude 'results' \
  -C "$REPO_ROOT" .

cat >"$DB_REMOTE_SCRIPT" <<EOF
#!/usr/bin/env bash
set -euo pipefail

ADMIN_PASSWORD=$(shell_quote "$ADMIN_PASSWORD")
OPENSEARCH_IMAGE=$(shell_quote "$OPENSEARCH_IMAGE")

sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates curl
if ! command -v docker >/dev/null 2>&1; then
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends docker.io
fi
sudo systemctl enable --now docker
sudo sysctl -w vm.max_map_count=262144
echo 'vm.max_map_count=262144' | sudo tee /etc/sysctl.d/99-opensearch.conf >/dev/null

sudo docker rm -f opensearch >/dev/null 2>&1 || true
sudo docker volume rm opensearch-single-data >/dev/null 2>&1 || true
sudo docker run -d \\
  --name opensearch \\
  --restart unless-stopped \\
  -p 9200:9200 \\
  -p 9600:9600 \\
  -e cluster.name=opensearch-cluster \\
  -e node.name=opensearch \\
  -e discovery.type=single-node \\
  -e bootstrap.memory_lock=true \\
  -e "OPENSEARCH_JAVA_OPTS=-Xms2g -Xmx2g" \\
  -e "OPENSEARCH_INITIAL_ADMIN_PASSWORD=\${ADMIN_PASSWORD}" \\
  --ulimit memlock=-1:-1 \\
  --ulimit nofile=65536:65536 \\
  -v opensearch-single-data:/usr/share/opensearch/data \\
  "\${OPENSEARCH_IMAGE}"

for _ in \$(seq 1 120); do
  if curl -k -fsS -u "admin:\${ADMIN_PASSWORD}" \\
    "https://localhost:9200/_cluster/health?wait_for_status=yellow&timeout=5s"; then
    echo
    echo "OpenSearch is ready"
    exit 0
  fi
  sleep 5
done

echo "OpenSearch did not become ready in time" >&2
sudo docker ps >&2 || true
sudo docker logs --tail=200 opensearch >&2 || true
exit 1
EOF

{
  cat <<EOF
#!/usr/bin/env bash
set -euo pipefail
set -o pipefail

REMOTE_REPO="\$HOME/${REMOTE_DIR}"
ARCHIVE="\$HOME/opensearch-experiment.tgz"
DB_PRIVATE_IP='__DB_PRIVATE_IP__'
OPENSEARCH_ADMIN_PASSWORD_VALUE=$(shell_quote "$ADMIN_PASSWORD")
OPENSEARCH_USER_PASSWORD_VALUE=$(shell_quote "$USER_PASSWORD")
LOG_REL=$(shell_quote "$LOG_REL")
PID_REL=$(shell_quote "$PID_REL")
DETACH=${DETACH}

EOF
  emit_bash_array ATTACK_ARGS "${ATTACK_ARGS[@]}"
  cat <<'EOF'

sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates curl

rm -rf "$REMOTE_REPO"
mkdir -p "$REMOTE_REPO"
tar -xzf "$ARCHIVE" -C "$REMOTE_REPO"
cd "$REMOTE_REPO"

bash -o pipefail -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
uv python install 3.10
uv venv --clear --python 3.10 .venv
uv pip install --python .venv/bin/python opensearch-py rich

for i in "${!ATTACK_ARGS[@]}"; do
  ATTACK_ARGS[$i]="${ATTACK_ARGS[$i]//__REMOTE_REPO__/$REMOTE_REPO}"
done

for _ in $(seq 1 120); do
  if curl -k -fsS -u "admin:${OPENSEARCH_ADMIN_PASSWORD_VALUE}" \
    "https://${DB_PRIVATE_IP}:9200/_cluster/health?wait_for_status=yellow&timeout=5s"; then
    echo
    echo "Remote OpenSearch is reachable from attacker VM"
    break
  fi
  sleep 5
done

if ! curl -k -fsS -u "admin:${OPENSEARCH_ADMIN_PASSWORD_VALUE}" \
  "https://${DB_PRIVATE_IP}:9200" >/dev/null; then
  echo "Attacker VM cannot reach OpenSearch at ${DB_PRIVATE_IP}:9200" >&2
  exit 1
fi

LOG_FILE="$REMOTE_REPO/$LOG_REL"
PID_FILE="$REMOTE_REPO/$PID_REL"
mkdir -p "$(dirname "$LOG_FILE")"

RUN_CMD=("$REMOTE_REPO/.venv/bin/unfilter-dls" "enumerate" "${ATTACK_ARGS[@]}")
{
  printf 'OPENSEARCH_HOST=%q OPENSEARCH_PORT=9200 OPENSEARCH_SCHEME=https OPENSEARCH_VERIFY_CERTS=false ' "$DB_PRIVATE_IP"
  printf 'OPENSEARCH_ADMIN_PASSWORD=%q OPENSEARCH_USER_PASSWORD=%q ' "$OPENSEARCH_ADMIN_PASSWORD_VALUE" "$OPENSEARCH_USER_PASSWORD_VALUE"
  printf '%q ' "${RUN_CMD[@]}"
  printf '\n'
} >"$(dirname "$LOG_FILE")/command.txt"

export OPENSEARCH_HOST="$DB_PRIVATE_IP"
export OPENSEARCH_PORT=9200
export OPENSEARCH_SCHEME=https
export OPENSEARCH_VERIFY_CERTS=false
export OPENSEARCH_ADMIN_PASSWORD="$OPENSEARCH_ADMIN_PASSWORD_VALUE"
export OPENSEARCH_USER_PASSWORD="$OPENSEARCH_USER_PASSWORD_VALUE"

if [[ "$DETACH" -eq 1 ]]; then
  nohup "${RUN_CMD[@]}" >"$LOG_FILE" 2>&1 &
  echo "$!" >"$PID_FILE"
  echo "Started attack in background"
  echo "PID file: $PID_FILE"
  echo "Log file: $LOG_FILE"
else
  "${RUN_CMD[@]}" 2>&1 | tee "$LOG_FILE"
fi
EOF
} >"$ATTACKER_REMOTE_SCRIPT"

chmod +x "$DB_REMOTE_SCRIPT" "$ATTACKER_REMOTE_SCRIPT"

ensure_network
ensure_instance "$DB_VM" "${DB_TAG},${SSH_TAG}"
ensure_instance "$ATTACKER_VM" "${ATTACKER_TAG},${SSH_TAG}"
wait_for_ssh "$DB_VM"
wait_for_ssh "$ATTACKER_VM"

DB_PRIVATE_IP="$("${GCLOUD[@]}" compute instances describe "$DB_VM" --zone "$ZONE" --format='get(networkInterfaces[0].networkIP)')"
log "database private IP: $DB_PRIVATE_IP"

# Patch the attacker setup script with the DB private IP after the DB VM exists.
python3 - "$ATTACKER_REMOTE_SCRIPT" "$DB_PRIVATE_IP" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
new = sys.argv[2]
text = path.read_text()
text = text.replace("__DB_PRIVATE_IP__", new)
path.write_text(text)
PY

log "copying repo archive and setup scripts"
run "${GCLOUD[@]}" compute scp "$ARCHIVE" "$ATTACKER_VM:~/opensearch-experiment.tgz" "${SCP_FLAGS[@]}"
run "${GCLOUD[@]}" compute scp "$DB_REMOTE_SCRIPT" "$DB_VM:~/setup_db_vm.sh" "${SCP_FLAGS[@]}"
run "${GCLOUD[@]}" compute scp "$ATTACKER_REMOTE_SCRIPT" "$ATTACKER_VM:~/setup_attacker_vm.sh" "${SCP_FLAGS[@]}"

log "setting up OpenSearch database VM"
run "${GCLOUD[@]}" compute ssh "$DB_VM" "${SSH_FLAGS[@]}" --command "chmod +x ~/setup_db_vm.sh && ~/setup_db_vm.sh"

log "setting up attacker VM and launching attack"
run "${GCLOUD[@]}" compute ssh "$ATTACKER_VM" "${SSH_FLAGS[@]}" --command "chmod +x ~/setup_attacker_vm.sh && ~/setup_attacker_vm.sh"

REMOTE_COMMAND_BASE="\$HOME/${REMOTE_DIR}"
# gcloud compute scp accepts the remote-home shorthand after the host prefix.
# shellcheck disable=SC2088
REMOTE_SCP_BASE="~/${REMOTE_DIR}"
IAP_ARG=""
if [[ "$USE_IAP" -eq 1 ]]; then
  IAP_ARG="--tunnel-through-iap "
fi
cat <<EOF

Launch complete.

Monitor:
  gcloud --project ${PROJECT_ID} compute ssh ${ATTACKER_VM} --zone ${ZONE} ${IAP_ARG}--command 'tail -f ${REMOTE_COMMAND_BASE}/${LOG_REL}'

Check process:
  gcloud --project ${PROJECT_ID} compute ssh ${ATTACKER_VM} --zone ${ZONE} ${IAP_ARG}--command 'ps -fp \$(cat ${REMOTE_COMMAND_BASE}/${PID_REL})'

Fetch results after completion:
  gcloud --project ${PROJECT_ID} compute scp ${IAP_ARG}${ATTACKER_VM}:${REMOTE_SCP_BASE}/${LOG_REL} ${LOCAL_FETCH_DIR}/
  gcloud --project ${PROJECT_ID} compute scp ${IAP_ARG}${ATTACKER_VM}:${REMOTE_SCP_BASE}/${STATS_REL} ${LOCAL_FETCH_DIR}/

Cleanup:
  gcloud --project ${PROJECT_ID} compute instances delete ${DB_VM} ${ATTACKER_VM} --zone ${ZONE}
  gcloud --project ${PROJECT_ID} compute firewall-rules delete ${PREFIX}-allow-ssh ${PREFIX}-allow-opensearch-attacker
  gcloud --project ${PROJECT_ID} compute networks subnets delete ${SUBNET} --region ${REGION}
  gcloud --project ${PROJECT_ID} compute networks delete ${NETWORK}
EOF
