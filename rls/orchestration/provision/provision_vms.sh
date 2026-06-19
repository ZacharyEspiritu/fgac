#!/usr/bin/env bash
# Provision the isolated GCP stack for an experiment and write a machine
# descriptor that the experiment driver (e.g. orchestration/run_existence_experiment.sh)
# consumes.
#
# This is the GCP-specific half. It does ONLY infrastructure — it creates the
# VMs + networking and uploads NO repo code, NO data, and NO experiment
# dependencies:
#   * create the isolated VPC + subnet + firewall rules
#   * create a bare PostgreSQL VM, a bare attacker VM, and (by default) a bare
#     noise VM, per the --config config.yml machine specs
#   * wait for cloud-init to complete on the VMs (the readiness check)
#   * emit a descriptor of *pointers* to the running machines
#
# It deliberately does NOT install PostgreSQL, the attacker toolchain, the repo,
# the venv, the dataset, or run the attack. Those steps run over a
# provider-agnostic transport (orchestration/util/_remote_transport.sh):
# orchestration/install/install_artifact_on_database.sh installs + configures PostgreSQL on the DB
# VM, orchestration/install/install_artifact_on_attacker.sh installs the attacker's OS deps +
# pushes the repo + builds the venv, then orchestration/run_existence_experiment.sh loads
# the data + runs the attack. That split lets a reviewer run the same experiment
# against machines they provisioned themselves — provide an equivalent ssh
# descriptor and skip this script entirely.
#
# Every resource is suffixed with -${RUN_ID} (see orchestration/util/_run_id_overlay.sh), so
# the stack is fully isolated from any other experiment in the project.
#
# Usage (--config <file> and --output <file> are REQUIRED):
#   export RUN_ID="exist-$(date +%s | tail -c8)"
#   bash orchestration/provision/provision_vms.sh --config orchestration/config/shared_config.yml \
#       --output results/machines/${RUN_ID}.yml
#
# The descriptor is YAML consumed via orchestration/util/_remote_transport.sh (which uses yq).
#
# Inputs:
#   --config <file>         - REQUIRED: config.yml to read (e.g. orchestration/config/shared_config.yml)
#   --output <path>         - REQUIRED: descriptor output path (e.g. results/machines/<RUN_ID>.yml)
#   --no-noise              - skip the noise VM. By DEFAULT a bare noise VM IS
#                             provisioned (alongside the DB + attacker) and recorded
#                             under `noise:` in the descriptor; it uses
#                             .noise.machine_type from the config if set, else the
#                             attacker machine type. Pass --no-noise for claims that
#                             have no noise generator (existence / mitigations / dbsize).
#   POSTGRES_SETUP_SCRIPT   - DB-VM setup utility basename under orchestration/provision/util
#                             (default setup_gcloud_postgres_vm.sh)
#
# On failure the partial stack is left in place; tear it down with:
#   bash orchestration/provision/cleanup_vms.sh --machines results/machines/${RUN_ID}.yml
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORCHESTRATION_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
UTIL_DIR="${SCRIPT_DIR}/util"
REPO_ROOT="$(cd "${ORCHESTRATION_DIR}/.." && pwd)"
cd "${REPO_ROOT}"   # repo sync resolves relative paths from here

OUTPUT_ARG=""
CONFIG_ARG=""
WANT_NOISE=1   # provision DB + attacker + noise by default; --no-noise skips the noise VM
while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)   CONFIG_ARG="$2"; shift 2 ;;
    --output)   OUTPUT_ARG="$2"; shift 2 ;;
    --no-noise) WANT_NOISE=0; shift ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

# Load shared infra/plumbing config from --config (REQUIRED; any claim's config.yml);
# sets the env + CONFIG_FILE=/dev/null so the shared GCP scripts use it.
# shellcheck source=/dev/null
. "${ORCHESTRATION_DIR}/util/_load_gcloud_config.sh" "${CONFIG_ARG}"
# shellcheck source=/dev/null
. "${ORCHESTRATION_DIR}/util/_run_id_overlay.sh"

PROJECT="${PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${REGION:-${ZONE%-*}}"
REMOTE_DIR="${REMOTE_DIR:-${REMOTE_BASE_DIR}/scratch}"
POSTGRES_SETUP_SCRIPT="${POSTGRES_SETUP_SCRIPT:-setup_gcloud_postgres_vm.sh}"
# Cross-region support: a claim may place the attacker in a different zone/subnet
# (e.g. C-R4). The loader defaults ATTACK_ZONE/REGION/SUBNET/SUBNET_RANGE + NOISE_ZONE
# to the DB-side values, so same-zone provisioning is byte-identical. CROSS_ZONE just
# gates the extra log + the per-role descriptor zone the attacker/noise are written at.
ATTACK_ZONE="${ATTACK_ZONE:-${ZONE}}"
ATTACK_REGION="${ATTACK_REGION:-${ATTACK_ZONE%-*}}"
ATTACK_SUBNET="${ATTACK_SUBNET:-${SUBNET}}"
ATTACK_SUBNET_RANGE="${ATTACK_SUBNET_RANGE:-${SUBNET_RANGE}}"
NOISE_ZONE="${NOISE_ZONE:-${ZONE}}"
CROSS_ZONE_NOTE=""
[[ "${ATTACK_ZONE}" != "${ZONE}" ]] && CROSS_ZONE_NOTE="  [cross-region attacker: ${ATTACK_ZONE} / ${ATTACK_SUBNET} (${ATTACK_SUBNET_RANGE})]"
if [[ -z "${OUTPUT_ARG}" ]]; then
  echo "--output <file> is required (e.g. --output results/machines/${RUN_ID:-<id>}.yml)." >&2
  exit 1
fi
MACHINES_FILE="${OUTPUT_ARG}"

log() { echo "[$(date -u +%FT%TZ)] [${RUN_ID:-no-run-id}] $*"; }

if [[ -z "${RUN_ID:-}" ]]; then
  echo "RUN_ID must be set (e.g. export RUN_ID=\"exist-\$(date +%s | tail -c8)\")." >&2
  exit 1
fi
if [[ -z "${PROJECT}" ]]; then
  echo "PROJECT is not set. Run 'gcloud config set project <id>' or set PROJECT." >&2
  exit 1
fi
command -v gcloud >/dev/null 2>&1 || { echo "gcloud CLI not found." >&2; exit 1; }
gcloud config set project "${PROJECT}" >/dev/null

log "=== Provision GCP stack ==="
log "  RUN_ID:       ${RUN_ID}"
log "  DB VM:        ${POSTGRES_VM} (${DB_MACHINE_TYPE}, boot=${BOOT_DISK_TYPE:-default})"
log "  Attacker VM:  ${ATTACK_VM} (${ATTACK_MACHINE_TYPE}, boot=${BOOT_DISK_TYPE:-default})"
if [[ "${WANT_NOISE}" == "1" ]]; then
  log "  Noise VM:     ${NOISE_VM} (${NOISE_MACHINE_TYPE:-${ATTACK_MACHINE_TYPE}}, boot=${BOOT_DISK_TYPE:-default})"
fi
log "  Network:      ${NETWORK}"
log "  Zone:         ${ZONE}"
[[ -n "${CROSS_ZONE_NOTE}" ]] && log "  Attacker:    ${CROSS_ZONE_NOTE}"
log "  Descriptor:   ${MACHINES_FILE}"

# ---- Stage 1: provision PostgreSQL VM (bare; no DB install) ---------------
# POSTGRES_INSTALL_DEPS=0 -> bare DB VM (cloud-init installs no PostgreSQL); the
# DB software install is deferred to orchestration/install/install_artifact_on_database.sh.
log "Stage 1: provision PostgreSQL VM (${POSTGRES_VM}) — bare (no DB install)"
if ! gcloud compute instances describe "${POSTGRES_VM}" --zone "${ZONE}" >/dev/null 2>&1; then
  POSTGRES_INSTALL_DEPS=0 bash "${UTIL_DIR}/${POSTGRES_SETUP_SCRIPT}"
else
  log "  ${POSTGRES_VM} already exists; skipping."
fi

# ---- Stage 2: provision attacker VM (bare; no deps, repo, or venv) --------
# ATTACK_INSTALL_DEPS=0 -> bare VM (cloud-init startup installs no toolchain).
# ATTACK_SETUP_REPO=0   -> skip the repo sync + venv. Both are deferred to
# orchestration/install/install_artifact_on_attacker.sh. The first call may fail on SSH-not-ready; the
# cloud-init wait below confirms readiness regardless.
log "Stage 2: provision attacker VM (${ATTACK_VM}) — bare (no deps/repo/venv)${CROSS_ZONE_NOTE}"
# orchestration/provision/util/setup_gcloud_attacker_vm.sh reads
# ZONE/REGION/SUBNET/SUBNET_RANGE from the env and creates the subnet it needs, so
# a cross-region claim just overrides those for THIS call — the attacker VM (and
# its second subnet, when the region differs) lands in the attacker zone.
# Same-zone: the per-role values equal the DB-side ones (no-op).
ATTACK_INSTALL_DEPS=0 ATTACK_SETUP_REPO=0 \
  ZONE="${ATTACK_ZONE}" REGION="${ATTACK_REGION}" SUBNET="${ATTACK_SUBNET}" SUBNET_RANGE="${ATTACK_SUBNET_RANGE}" \
  bash "${UTIL_DIR}/setup_gcloud_attacker_vm.sh" || true

# ---- Stage 2b: provision noise VM (default; bare; --no-noise to skip) -----
# NOISE_INSTALL_DEPS=0 / NOISE_SETUP_REPO=0 -> bare VM (no toolchain/repo/venv);
# those are deferred to orchestration/install/install_artifact_on_noise.sh. Provisioned by default;
# pass --no-noise for claims with no noise generator (existence/mitigations/dbsize).
if [[ "${WANT_NOISE}" == "1" ]]; then
  log "Stage 2b: provision noise VM (${NOISE_VM}) — bare (no deps/repo/venv)"
  NOISE_INSTALL_DEPS=0 NOISE_SETUP_REPO=0 bash "${UTIL_DIR}/setup_gcloud_noise_vm.sh" || true
fi

# ---- Stage 3: wait for cloud-init on the VMs ------------------------------
log "Stage 3: wait for cloud-init to complete on the VMs"
wait_for_cloud_init() {
  local vm="$1" label="$2" z="${3:-${ZONE}}"   # z: per-role zone (attacker may differ)
  local deadline=$(( $(date +%s) + 1800 ))   # 30-minute ceiling
  log "  waiting for ${label} (${vm}) cloud-init..."
  while true; do
    if gcloud compute ssh "${vm}" --zone "${z}" \
         --ssh-flag="-o ConnectTimeout=10" \
         --command 'sudo systemctl status google-startup-scripts 2>&1 | grep -q "Active: inactive (dead)" && ! pgrep -x apt-get >/dev/null 2>&1 && echo ready' \
         2>/dev/null | grep -q ready; then
      log "  ${label} cloud-init done."
      break
    fi
    if [[ $(date +%s) -gt ${deadline} ]]; then
      log "ERROR: ${label} cloud-init did not finish within 30 minutes." >&2
      exit 1
    fi
    sleep 20
  done
}
wait_for_cloud_init "${POSTGRES_VM}" "postgres" "${ZONE}"
wait_for_cloud_init "${ATTACK_VM}"   "attacker" "${ATTACK_ZONE}"
if [[ "${WANT_NOISE}" == "1" ]]; then
  wait_for_cloud_init "${NOISE_VM}" "noise" "${NOISE_ZONE}"
fi

# ---- Stage 4: resolve the DB internal IP for the descriptor ---------------
# No connection-level PostgreSQL check here: that would need psql on the
# attacker, which is bare until orchestration/install/install_artifact_on_attacker.sh runs. DB readiness is
# the DB VM's cloud-init finishing (confirmed in Stage 3) — its startup script
# installs and starts PostgreSQL before cloud-init reports done.
log "Stage 4: resolve DB internal IP"
DB_IP="$(gcloud compute instances describe "${POSTGRES_VM}" \
  --zone "${ZONE}" --format='get(networkInterfaces[0].networkIP)')"
log "  DB internal IP: ${DB_IP}"

# ---- Stage 5: emit the machine descriptor (the "pointers") ----------------
mkdir -p "$(dirname "${MACHINES_FILE}")"
cat > "${MACHINES_FILE}" <<EOF
# Machine descriptor (YAML) for the existence-family experiment.
# Written by orchestration/provision/provision_vms.sh; consumed by orchestration/install/install_artifact_on_attacker.sh
# and orchestration/run_existence_experiment.sh via orchestration/util/_remote_transport.sh (yq-parsed).
# Generated $(date -u +%FT%TZ).
transport: gcloud
project: ${PROJECT}
zone: ${ZONE}
remote_dir: ${REMOTE_DIR}
run_id: ${RUN_ID}
config_file: ${GCLOUD_CONFIG}
provisioner: orchestration/provision/provision_vms.sh
db:
  vm: ${POSTGRES_VM}
  zone: ${ZONE}
  internal_addr: ${DB_IP}
attacker:
  vm: ${ATTACK_VM}
  zone: ${ATTACK_ZONE}
EOF
if [[ "${WANT_NOISE}" == "1" ]]; then
cat >> "${MACHINES_FILE}" <<EOF
noise:
  vm: ${NOISE_VM}
  zone: ${NOISE_ZONE}
EOF
fi

log "=== Provisioning complete (bare VMs; no repo, no data) ==="
log "  Descriptor: ${MACHINES_FILE}"
log "  Next: install the DB + the attacker artifact, then run the experiment:"
log "    bash orchestration/install/install_artifact_on_database.sh --config ${GCLOUD_CONFIG} --machines ${MACHINES_FILE}"
log "    bash orchestration/install/install_artifact_on_attacker.sh --config ${GCLOUD_CONFIG} --machines ${MACHINES_FILE}"
if [[ "${WANT_NOISE}" == "1" ]]; then
log "    bash orchestration/install/install_artifact_on_noise.sh    --config ${GCLOUD_CONFIG} --machines ${MACHINES_FILE}"
fi
log "    bash orchestration/run_existence_experiment.sh     --config ${GCLOUD_CONFIG} --job <existence|range> --machines ${MACHINES_FILE}"
log "  Tear the stack down when done:"
log "    bash orchestration/provision/cleanup_vms.sh --machines ${MACHINES_FILE}"
