# shellcheck shell=bash
# Shared GCP config loader for the provision/install/run split (config strategy B:
# one config.yml per claim + this generalized loader).
#
# Reads the COMMON infra keys (project / zone / network / machine types / disks /
# image / postgres password / dataset) from a per-claim config.yml via `yq` and
# exports them, then sets the COMMON plumbing constants the shared GCP scripts and
# the run-id overlay expect, and finally points CONFIG_FILE at /dev/null so those
# scripts skip their own .env sourcing and use this environment instead.
#
# Each claim's thin loader (e.g. _load_existence_config.sh) sources THIS file with
# the config path as $1, then reads its own experiment-specific keys with _cfg_yq
# and sets any claim-specific plumbing.
#
# Provides to the sourcing loader:
#   GCLOUD_CONFIG            absolute path of the config file (NOT exported)
#   _cfg_yq '<path>' [def]   scalar reader (quote-stripped, default for null/missing)
#
# The config path is REQUIRED (passed from the caller's --config flag); there is
# no default.

GCLOUD_CONFIG="${1:-}"
if [[ -z "${GCLOUD_CONFIG}" ]]; then
  echo "--config <file> is required (e.g. --config orchestration/<claim>_config.yml)." >&2
  return 1 2>/dev/null || exit 1
fi
if ! command -v yq >/dev/null 2>&1; then
  echo "yq not found in PATH (required to read ${GCLOUD_CONFIG})." >&2
  return 1 2>/dev/null || exit 1
fi
if [[ ! -f "${GCLOUD_CONFIG}" ]]; then
  echo "config not found: ${GCLOUD_CONFIG}" >&2
  return 1 2>/dev/null || exit 1
fi
# Absolute path, so child scripts (and the descriptor) can reference it.
GCLOUD_CONFIG="$(cd "$(dirname "${GCLOUD_CONFIG}")" && pwd)/$(basename "${GCLOUD_CONFIG}")"

# yq scalar reader with quote-stripping (works with mikefarah v4 and python-yq)
# and an optional default for missing/null keys.
_cfg_yq() {
  local out; out="$(yq "$1" "${GCLOUD_CONFIG}" 2>/dev/null)" || out=""
  out="${out%\"}"; out="${out#\"}"
  if [[ -z "${out}" || "${out}" == "null" ]]; then printf '%s' "${2:-}"; else printf '%s' "${out}"; fi
}

set -a

# ---- common infra: from config.yml ----------------------------------------
PROJECT="$(_cfg_yq '.project')"
ZONE="$(_cfg_yq '.zone' us-central1-a)"
NETWORK="$(_cfg_yq '.network' rls-net)"
SUBNET="$(_cfg_yq '.subnet' rls-subnet)"
SUBNET_RANGE="$(_cfg_yq '.subnet_range' 10.10.0.0/24)"
# ---- per-role zones / subnets (cross-region claims, e.g. C-R4) ------------
# Default to the DB-side zone/subnet, so same-zone claims are byte-identical. A
# claim that places the attacker in a DIFFERENT zone (C-R4 cross-region) sets
# attacker.zone / region / subnet; provision/provision_vms.sh then creates the attacker
# VM there (a SECOND subnet in the same VPC) and records the per-role zones in the
# descriptor (the transport already honors per-role zones). Noise defaults to the
# DB zone (co-located, so the DB-CPU controller drives load locally).
ATTACK_ZONE="$(_cfg_yq '.attacker.zone' "${ZONE}")"
ATTACK_REGION="$(_cfg_yq '.attacker.region' "${ATTACK_ZONE%-*}")"
ATTACK_SUBNET="$(_cfg_yq '.attacker.subnet' "${SUBNET}")"
ATTACK_SUBNET_RANGE="$(_cfg_yq '.attacker.subnet_range' "${SUBNET_RANGE}")"
NOISE_ZONE="$(_cfg_yq '.noise.zone' "${ZONE}")"
# pg_hba allow CIDR for orchestration/install/install_artifact_on_database.sh. Empty => it
# falls back to subnet_range; a cross-zone claim sets a supernet covering the DB
# + attacker subnets.
DB_ALLOW_CIDR="$(_cfg_yq '.db_allow_cidr')"
DB_MACHINE_TYPE="$(_cfg_yq '.db.machine_type' c4-standard-16)"
DB_DISK_SIZE="$(_cfg_yq '.db.disk_size' 200GB)"
ATTACK_MACHINE_TYPE="$(_cfg_yq '.attacker.machine_type' c4-standard-16)"
ATTACK_DISK_SIZE="$(_cfg_yq '.attacker.disk_size' 200GB)"
# Noise VM specs (optional; only claims with a noise generator declare `.noise.*`).
# Empty => the claim has no noise VM; orchestration/provision/util/setup_gcloud_noise_vm.sh
# falls back to the attacker specs if a noise VM is nonetheless requested.
NOISE_MACHINE_TYPE="$(_cfg_yq '.noise.machine_type')"
NOISE_DISK_SIZE="$(_cfg_yq '.noise.disk_size')"
BOOT_DISK_TYPE="$(_cfg_yq '.boot_disk_type' hyperdisk-balanced)"
IMAGE_FAMILY="$(_cfg_yq '.image_family' debian-12)"
IMAGE_PROJECT="$(_cfg_yq '.image_project' debian-cloud)"
POSTGRES_PASSWORD="$(_cfg_yq '.postgres_password')"
RLS_POLICY="$(_cfg_yq '.rls_policy' join)"
PATIENTS="$(_cfg_yq '.dataset.patients' 1000000)"
DOCTORS="$(_cfg_yq '.dataset.doctors' 10000)"
SITES="$(_cfg_yq '.dataset.sites' 5)"

# ---- DB provisioning/install selection ------------------------------------
# Most claims provision a bare DB VM and install PostgreSQL via a separate step
# (orchestration/install/install_artifact_on_database.sh). The provision-time setup script
# is a basename under orchestration/provision/util; the install-time script is a basename
# under orchestration/install. C-R5 TDE uses the normal bare DB provisioner with an attached
# raw data disk, then selects install_artifact_on_database_tde.sh to LUKS-format
# the disk and install PostgreSQL over the transport.
POSTGRES_SETUP_SCRIPT="$(_cfg_yq '.postgres_setup_script' setup_gcloud_postgres_vm.sh)"
# DB install step basename under orchestration/install/ (default = plain PostgreSQL).
# C-R5 TDE selects install_artifact_on_database_tde.sh (basename under orchestration/install/;
# LUKS + PG on the encrypted disk).
DB_INSTALL_SCRIPT="$(_cfg_yq '.db_install_script' install_artifact_on_database.sh)"
# Optional escape hatch for a provisioner that already installed the DB software.
DB_INSTALL_BUNDLED="$(_cfg_yq '.db_install_bundled' 0)"
# TDE: the raw secondary disk attached at provision time + LUKS-encrypted at install.
TDE_DISK_DEVICE_NAME="$(_cfg_yq '.tde.disk_device_name')"
TDE_DATA_DISK_SIZE="$(_cfg_yq '.tde.data_disk_size')"
TDE_DATA_DISK_TYPE="$(_cfg_yq '.tde.data_disk_type')"
# Target block device for the LUKS volume. Empty =>
# orchestration/install/install_artifact_on_database_tde.sh defaults to the GCP by-id path;
# bring-your-own (ssh) sets it to a spare device (/dev/sdb).
TDE_DEVICE="$(_cfg_yq '.tde.device')"

# ---- common plumbing (not user-tunable) -----------------------------------
RUN_LABEL="${RUN_LABEL:-rls-experiment}"
DB_ENGINE="postgres"
SSH_CIDR="0.0.0.0/0"
# VM base names + firewall tags (the run-id overlay suffixes these with -${RUN_ID}).
# POSTGRES_VM is config-overridable (default rls-postgres) so the TDE claim can use
# a distinct DB VM name (rls-postgres-tde) alongside a concurrent baseline run.
POSTGRES_VM="$(_cfg_yq '.postgres_vm' rls-postgres)"; ATTACK_VM="rls-attacker"; NOISE_VM="rls-noise"
POSTGRES_TAG="rls-postgres"; ATTACK_TAG="rls-attacker"; NOISE_TAG="rls-noise"
POSTGRES_READY_RETRIES="60"; POSTGRES_READY_SLEEP="5"
REMOTE_BASE_DIR="rls-dir"; REMOTE_DIR=""
# orchestration/provision/util/setup_gcloud_*_vm.sh reference these even when they skip the
# repo sync; blank => they fall back to the local checkout (the split pushes the
# repo separately via orchestration/install/install_artifact_on_{attacker,noise}.sh).
LOCAL_REPO_DIR=""; REPO_URL=""
ATTACKER_USER="doctor_s1_00000"; ATTACKER_PASSWORD="${ATTACKER_USER}"
ATTACK_DB="rls"; ADMIN_DB="rls"

set +a

# Mark YAML-derived environments so child setup utilities reject standalone
# direct-GCloud invocation while using the values exported above.
export CONFIG_FILE=/dev/null
